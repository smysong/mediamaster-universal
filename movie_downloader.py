import os
import re
import json
import logging
import sqlite3
import requests
from typing import List
from urllib.parse import urljoin, urlparse, unquote, urlencode, parse_qs
from bs4 import BeautifulSoup
import configparser

# 配置日志功能
logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 创建一个ConfigParser对象并读取配置文件
config = configparser.ConfigParser()
config_path = '/config/config.ini'  # 假设配置文件位于当前目录下
if not os.path.exists(config_path):
    logger.error(f"配置文件 {config_path} 不存在")
    exit(1)
config.read(config_path, encoding='utf-8')

# 从配置文件中读取必要的配置项
base_url = config.get("urls", "movie_url", fallback="https://www.hdbthd.com")
login_page_url = f"{base_url}/member.php?mod=logging&action=login"
login_url = f"{base_url}/member.php?mod=logging&action=login&loginsubmit=yes&inajax=1"
search_url = f"{base_url}/search.php?mod=forum"
user_profile_url = f"{base_url}/home.php?mod=space"  # 用户个人页面URL
db_path = config.get('database', 'db_path', fallback='')
if not db_path:
    logger.error("配置文件中未找到数据库路径")
    exit(1)
username = config.get("resources", "login_username", fallback="")
password = config.get("resources", "login_password", fallback="")
exclude_keywords_str = config.get("resources", "exclude_keywords", fallback="")
exclude_keywords = [kw.strip().lower() for kw in exclude_keywords_str.split(',') if kw.strip()]
preferred_resolution = config.get("resources", "preferred_resolution", fallback="")
fallback_resolution = config.get("resources", "fallback_resolution", fallback="")

# 创建会话对象并设置默认HTTP头信息
session = requests.Session()
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Content-Type': 'application/x-www-form-urlencoded; charset=GBK',  # 显式指定字符集
    'Origin': base_url,
    'Connection': 'keep-alive',
    'Referer': base_url,
    'Accept-Encoding': 'gzip, deflate, br',
}
session.headers.update(headers)

class MovieInfoExtractor:
    def __init__(self, db_path, config):
        self.db_path = db_path
        self.config = config

    def extract_movie_info(self):
        """从数据库读取订阅电影信息"""
        all_movie_info = []
        try:
            # 连接到 SQLite 数据库，并显式设置 text_factory 为 str
            with sqlite3.connect(self.db_path, isolation_level=None, detect_types=sqlite3.PARSE_DECLTYPES) as conn:
                conn.text_factory = str  # 确保返回的是 Unicode 字符串
                cursor = conn.cursor()
                cursor.execute('SELECT title, year FROM MISS_MOVIES')
                movies = cursor.fetchall()

                for title, year in movies:
                    # 添加调试信息，打印出读取到的每一行数据
                    logger.debug(f"读取到的电影信息: 标题={title}, 年份={year}")
                    all_movie_info.append({
                        "标题": title,
                        "年份": year
                    })

            logger.info("读取订阅电影信息完成")
            return all_movie_info
        except Exception as e:
            logger.error(f"提取电影信息时发生错误: {e}")
            return None

def load_and_check_cookies(session, user_profile_url):
    """加载并检查现有的cookies"""
    if os.path.exists('/tmp/movie_cookies.json'):
        with open('/tmp/movie_cookies.json', 'r') as file:
            cookies_dict = json.load(file)
            session.cookies.update(cookies_dict)
            response = session.get(user_profile_url)
            if response.status_code == 200 and username in response.text:
                logger.info("Cookies有效，无需重新登录")
                return True
            else:
                logger.warning("Cookies无效，需要重新登录")
                return False
    else:
        logger.info("未找到现有cookies，需要重新登录")
        return False

def login(session, username, password):
    """执行登录操作"""
    response = session.get(login_page_url)
    if response.status_code != 200:
        logger.error(f"获取登录页面失败，状态码: {response.status_code}")
        return False
    
    soup = BeautifulSoup(response.text, 'html.parser')
    formhash_tag = soup.find('input', {'name': 'formhash'})
    if not formhash_tag or 'value' not in formhash_tag.attrs:
        logger.error("未能找到formhash")
        return False
    
    formhash = formhash_tag['value']
    form_data = {
        'formhash': formhash,
        'referer': base_url,
        'loginfield': 'username',
        'username': username,
        'password': password,
        'questionid': '0',
        'answer': '',
        'cookietime': '2592000'
    }

    login_response = session.post(login_url, data=form_data)
    if login_response.status_code == 200 and '欢迎您回来' in login_response.text:
        logger.info("登录成功")
        # 保存cookies到文件
        with open('/tmp/movie_cookies.json', 'w') as file:
            json.dump(requests.utils.dict_from_cookiejar(session.cookies), file)
        return True
    else:
        logger.error(f"登录失败，状态码: {login_response.status_code}")
        return False

def get_formhash_for_search(session, url):
    """为搜索请求获取formhash"""
    try:
        response = session.get(url)
        if response.status_code != 200:
            logger.error(f"获取搜索formhash失败，状态码: {response.status_code}")
            return None
        
        soup = BeautifulSoup(response.text, 'html.parser')
        formhash_tag = soup.find('input', {'name': 'formhash'})
        if not formhash_tag or 'value' not in formhash_tag.attrs:
            logger.error("未能找到formhash")
            return None
        
        return formhash_tag['value']
    except Exception as e:
        logger.error(f"获取搜索formhash时发生异常: {e}")
        return None

def encode_gbk(data):
    """将表单数据编码为 GBK"""
    return {key: value.encode('gbk').decode('latin1') for key, value in data.items()}

def preserve_encoding_in_redirect(response, *args, **kwargs):
    """在重定向前保持原始编码"""
    if response.is_redirect:
        # 获取Location头中的URL
        redirect_url = response.headers.get('Location')
        if redirect_url:
            # 确保重定向URL是绝对路径
            redirect_url = urljoin(base_url, redirect_url)

            # 解析重定向URL
            parsed_url = urlparse(redirect_url)
            query_params = parse_qs(parsed_url.query, encoding='latin1')

            # 对于特定的参数（如'kw'），保持其原始编码
            if 'kw' in query_params:
                query_params['kw'] = [param.encode('latin1').decode('gbk') for param in query_params['kw']]
            
            # 重新构建查询字符串
            new_query_string = urlencode(query_params, doseq=True, encoding='gbk', errors='surrogateescape')
            final_redirect_url = parsed_url._replace(query=new_query_string).geturl()

            logger.debug(f"重定向到: {final_redirect_url}")
            # 修改响应对象的Location头
            response.headers['Location'] = final_redirect_url

def perform_search(session, search_url, formhash, keyword):
    """执行搜索操作，只使用标题作为关键词"""
    form_data = {
        'formhash': formhash,
        'srchtxt': keyword,
        'searchsubmit': 'yes'
    }

    # 将表单数据编码为 GBK
    encoded_form_data = urlencode(form_data, encoding='gbk')

    logger.debug(f"提交的表单数据: {encoded_form_data}")
    logger.info(f"开始搜索： {keyword} ")
    # 禁用自动重定向，并添加钩子函数
    search_response = session.post(
        search_url, 
        data=encoded_form_data, 
        headers=headers,
        allow_redirects=False,
        hooks={'response': preserve_encoding_in_redirect}
    )

    # 手动处理重定向
    while search_response.is_redirect:
        redirect_url = search_response.headers.get('Location')
        if not redirect_url:
            logger.error("未找到重定向URL")
            return None

        # 确保重定向URL是绝对路径
        base_url = search_response.request.url
        redirect_url = urljoin(base_url, redirect_url)

        # 发送GET请求到重定向URL
        search_response = session.get(redirect_url, allow_redirects=False, hooks={'response': preserve_encoding_in_redirect})

    if search_response.status_code == 200:
        # 强制设置响应内容的编码为 GBK
        search_response.encoding = 'gbk'
        html_content = search_response.text
        return html_content
    else:
        logger.error(f"最终请求失败，状态码: {search_response.status_code}")
        return None

def should_exclude(result_title: str, exclude_keywords: List[str]) -> bool:
    """检查标题是否包含任何排除关键字"""
    return any(keyword.lower() in result_title.lower() for keyword in exclude_keywords)

def parse_file_size(size_str: str) -> float:
    """将文件大小字符串转换为浮点数（以GB为单位）"""
    size_str = size_str.strip().upper()
    match = re.search(r'(\d+(\.\d+)?)\s*(GB|MB)', size_str)
    if match:
        size, unit = float(match.group(1)), match.group(3)
        if unit == 'MB':
            return size / 1024  # 将MB转换为GB
        return size
    return None

def parse_search_results(html_content, title, year, exclude_keywords, preferred_resolution, fallback_resolution):
    """解析搜索结果，并根据标题、年份及分辨率情况进行匹配，返回所有符合条件的链接"""
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        results = []

        for li in soup.find_all('li', class_='pbw'):
            h3 = li.find('h3', class_='xs3')
            if not h3 or not h3.find('a'):
                continue
            
            a_tag = h3.find('a')
            link = a_tag['href']
            result_title = a_tag.get_text(strip=True)
            
            # 检查年份是否在结果标题中
            if year and str(year) not in result_title:
                continue
            
            if title.lower() not in result_title.lower() or should_exclude(result_title, exclude_keywords):
                continue
            
            results.append({
                'title': result_title,
                'link': link
            })

        # 分别筛选符合首选分辨率和备用分辨率的结果
        preferred_results = []
        fallback_results = []
        for result in results:
            if preferred_resolution and preferred_resolution.lower() in result['title'].lower():
                preferred_results.append(result)
            elif fallback_resolution and fallback_resolution.lower() in result['title'].lower():
                fallback_results.append(result)
        # 如果有首选分辨率的结果，则返回这些结果；否则返回备用分辨率的结果
        if preferred_results:
            return preferred_results
        else:
            logger.info(f"未匹配到首选分辨率结果，使用备用分辨率进行匹配")
            return fallback_results
    except Exception as e:
        logger.error(f"解析搜索结果时发生异常: {e}")
        return []

def get_and_parse_link(session, link, title, base_url):
    """发送GET请求并解析选定链接的内容，提取下载链接"""
    # 确保链接是绝对URL
    if not urlparse(link).netloc:
        link = urljoin(base_url, link)
    
    try:
        response = session.get(link)
        if response.status_code != 200:
            logger.error(f"解析链接失败，状态码: {response.status_code}")
            return None, []
        
        # 强制设置响应内容的编码为 GBK
        response.encoding = 'gbk'
        # 解码为unicode
        html_content = response.text
        soup = BeautifulSoup(html_content, 'html.parser')     

        # 查找所有符合条件的<a>标签
        download_links = []
        for a_tag in soup.select('div.button span[id^="attach_"] a[href]'):
            href = a_tag['href']
            text = a_tag.get_text(strip=True).lower()
            
            # 检查链接文本或href中是否包含.torrent
            if '.torrent' in text or '.torrent' in href:
                # 提取文件名
                filename_match = re.search(r'(.*?\.torrent)', text)
                if filename_match:
                    filename = filename_match.group(1)
                else:
                    # 如果链接文本中没有明确的文件名，尝试从href中提取
                    filename = unquote(href.split('/')[-1].split('?')[0])
                
                # 拼接完整的下载链接
                full_link = urljoin(base_url, href)
                download_links.append({'link': full_link, 'text': text, 'filename': filename})
                logger.debug(f"找到下载链接: {title}, 链接: {full_link}")

        if not download_links:
            logger.warning(f"未找到下载链接: {title}")
        
        return str(soup), download_links
    except requests.RequestException as e:
        logger.error(f"解析链接时发生请求异常: {e}")
        return None, []

def download_file(session, download_info, download_dir='/Torrent'):
    """下载文件到指定的下载目录，并使用原始文件名"""
    # 确保下载目录存在，如果不存在则创建
    if not os.path.exists(download_dir):
        os.makedirs(download_dir)
        logger.info(f"创建下载目录: {download_dir}")

    # 构建完整的文件路径
    full_path = os.path.join(download_dir, download_info['filename'])

    try:
        response = session.get(download_info['link'], stream=True)
        if response.status_code != 200:
            logger.error(f"下载文件失败，状态码: {response.status_code}")
            return False
        
        with open(full_path, 'wb') as file:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:  # 检查chunk是否非空
                    file.write(chunk)
        logger.debug(f"文件下载成功: {full_path}")
        return True
    except requests.RequestException as e:
        logger.error(f"下载文件时发生请求异常: {e}")
        return False

def search_and_download_movie(movie_info, formhash):
    """搜索并下载电影"""
    title = movie_info.get("标题")
    year = movie_info.get("年份")
    keyword = title  # 只使用标题作为关键词
    html_content = perform_search(session, search_url, formhash, keyword)
    if not html_content:
        logger.error(f"搜索 {keyword} 失败")
        return
    
    search_results = parse_search_results(html_content, title, year, exclude_keywords, preferred_resolution, fallback_resolution)
    if not search_results:
        logger.info(f" {keyword} ：没有找到匹配的资源")
        return
    
    for result in search_results:
        _, download_links = get_and_parse_link(session, result['link'], title, base_url)
        if not download_links:
            logger.warning(f"未找到下载链接: {result['title']}")
            continue
        
        for dl in download_links:
            if download_file(session, dl, download_dir='/Torrent'):
                logger.info(f"已成功下载 {title} ")
                return  # 成功下载后立即返回，不再处理其他结果

def main():
    extractor = MovieInfoExtractor(db_path, config)
    movie_info_list = extractor.extract_movie_info()

    if not movie_info_list:
        logger.error("没有找到需要处理的电影信息")
        return

    # 检查并加载cookies
    if not load_and_check_cookies(session, user_profile_url):
        # 登录时显式传递login_page_url
        if not login(session, username, password):
            logger.error("登录失败，程序终止")
            return
    
    for movie_info in movie_info_list:
        # 在每次搜索之前，获取新的formhash
        search_formhash = get_formhash_for_search(session, user_profile_url)
        if not search_formhash:
            logger.error("无法继续，因为没有获取到搜索用的formhash")
            continue

        search_and_download_movie(movie_info, search_formhash)

if __name__ == '__main__':
    main()