import requests
from bs4 import BeautifulSoup
import json
import configparser
import os
import logging
import sqlite3  # 导入 sqlite3 模块
from typing import List, Dict, Tuple
import re  # 导入正则表达式模块
from urllib.parse import urljoin  # 导入用于拼接URL的函数

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
base_url = config.get("urls", "tv_url", fallback="https://www.bthdtv.com")
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
    'Content-Type': 'application/x-www-form-urlencoded',
    'Origin': base_url,
    'Connection': 'keep-alive',
    'Referer': base_url,
    'Accept-Encoding': 'gzip, deflate, br'
}
session.headers.update(headers)

class TVInfoExtractor:
    def __init__(self, db_path, config):
        self.db_path = db_path
        self.config = config

    def extract_tv_info(self) -> List[Dict[str, str]]:
        """从数据库读取缺失的电视节目信息"""
        all_tv_info = []
        try:
            with sqlite3.connect(self.db_path) as conn:  # 使用 sqlite3 连接数据库
                cursor = conn.cursor()
                cursor.execute('SELECT title, missing_episodes FROM MISS_TVS')
                tvs = cursor.fetchall()

                for title, missing_episodes_str in tvs:
                    missing_episodes = [int(ep.strip()) for ep in missing_episodes_str.split(',') if ep.strip()]
                    min_episode_num = min(missing_episodes) if missing_episodes else 1
                    formatted_episode_number = f'{"0" if min_episode_num < 10 else ""}{min_episode_num}'
                    
                    resolution = self.config.get("resources", "preferred_resolution", fallback="")
                    all_tv_info.append({
                        "剧集": title,
                        "分辨率": resolution,
                        "集数": formatted_episode_number,
                        "missing_episodes": missing_episodes  # 添加缺失的集数列表
                    })
        except sqlite3.Error as e:
            logger.error(f"数据库操作失败: {e}")
            exit(1)

        return all_tv_info

def load_and_check_cookies(session, user_profile_url):
    """加载并检查现有的cookies"""
    if os.path.exists('/tmp/tvshow_cookies.json'):
        with open('/tmp/tvshow_cookies.json', 'r') as file:
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
        with open('/tmp/tvshow_cookies.json', 'w') as file:
            json.dump(requests.utils.dict_from_cookiejar(session.cookies), file)
        return True
    else:
        logger.error(f"登录失败，状态码: {login_response.status_code}")
        return False

def get_formhash(session, url):
    """获取formhash"""
    response = session.get(url)
    if response.status_code != 200:
        logger.error(f"获取formhash失败，状态码: {response.status_code}")
        return None
    
    soup = BeautifulSoup(response.text, 'html.parser')
    formhash_tag = soup.find('input', {'name': 'formhash'})
    if not formhash_tag or 'value' not in formhash_tag.attrs:
        logger.error("未能找到formhash")
        return None
    
    return formhash_tag['value']

def perform_search(session, search_url, formhash, keyword):
    """执行搜索操作"""
    form_data = {
        'formhash': formhash,
        'srchtxt': keyword,
        'searchsubmit': 'yes'
    }
    
    search_response = session.post(search_url, data=form_data)
    if search_response.status_code == 200:
        return search_response.text
    else:
        logger.error(f"搜索请求失败，状态码: {search_response.status_code}")
        return None

def parse_episode_range(range_str: str) -> Tuple[int, int, bool]:
    """解析集数范围字符串，返回起始和结束集数以及是否为全集"""
    range_str = range_str.strip().replace('第', '').replace('集', '')
    
    full_match = re.search(r"全(\d{1,2})集", range_str)
    if full_match:
        total_episodes = int(full_match.group(1))
        return 1, total_episodes, True
    
    if '-' in range_str:
        start, end = range_str.split('-')
        return int(start), int(end), False
    elif ',' in range_str:
        episodes = [int(ep.strip()) for ep in range_str.split(',')]
        return min(episodes), max(episodes), False
    else:
        return int(range_str), int(range_str), False

def is_episode_in_range(episode: int, range_str: str) -> bool:
    """检查指定集数是否在给定的集数范围内"""
    start, end, _ = parse_episode_range(range_str)
    return start <= episode <= end

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

def parse_search_results(html_content, title, episode_number, exclude_keywords, preferred_resolution, fallback_resolution):
    """解析搜索结果，并根据集数范围及分辨率情况进行匹配，返回所有符合条件的链接"""
    soup = BeautifulSoup(html_content, 'html.parser')
    results = []

    for li in soup.find_all('li', class_='pbw'):
        h3 = li.find('h3', class_='xs3')
        if not h3 or not h3.find('a'):
            continue
        
        a_tag = h3.find('a')
        link = a_tag['href']
        result_title = a_tag.get_text(strip=True)
        
        if title.lower() not in result_title.lower() or should_exclude(result_title, exclude_keywords):
            continue
        
        match = re.search(r"(?:第(\d{1,2}-\d{1,2}|\d{1,2},\d{1,2}|\d{1,2})集|全(\d{1,2})集)", result_title)
        if match:
            if match.group(2):
                # 全集资源
                file_size_str = result_title.split()[-1]
                file_size = parse_file_size(file_size_str)
                if file_size is not None:
                    results.append({
                        'link': link, 
                        'title': result_title, 
                        'file_size': file_size,
                        'episode_range': (1, int(match.group(2)))  # 记录全集范围
                    })
                    continue
            
            range_str = match.group(1)
            start, end, _ = parse_episode_range(range_str)
            if is_episode_in_range(int(episode_number), range_str):
                file_size_str = result_title.split()[-1]
                file_size = parse_file_size(file_size_str)
                if file_size is not None:
                    results.append({
                        'link': link, 
                        'title': result_title, 
                        'file_size': file_size,
                        'episode_range': (start, end)  # 记录多集范围
                    })
                    continue
        elif f"第{episode_number}集" in result_title:
            file_size_str = result_title.split()[-1]
            file_size = parse_file_size(file_size_str)
            if file_size is not None:
                results.append({
                    'link': link, 
                    'title': result_title, 
                    'file_size': file_size,
                    'episode_range': (int(episode_number), int(episode_number))  # 单集
                })

    # 筛选符合分辨率要求的结果
    preferred_results = []
    fallback_results = []

    for result in results:
        if preferred_resolution and preferred_resolution.lower() in result['title'].lower():
            preferred_results.append(result)
        elif fallback_resolution and fallback_resolution.lower() in result['title'].lower():
            fallback_results.append(result)

    # 如果没有找到符合首选分辨率的结果，则尝试匹配备用分辨率
    if not preferred_results:
        logger.info(f"未匹配到首选分辨率结果，使用备用分辨率进行匹配")
    else:
        logger.info(f"已匹配到首选分辨率结果")

    # 返回结果时优先返回首选分辨率的结果，如果没有则返回备用分辨率的结果
    return preferred_results if preferred_results else fallback_results
    
def get_and_parse_link(session, link, title, base_url):
    """发送GET请求并解析选定链接的内容，确保所有链接都是完整URL，并提取下载链接"""
    try:
        response = session.get(link)
        if response.status_code != 200:
            logger.error(f"解析链接失败，状态码: {response.status_code}")
            return None, []
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 提取并补全所有<a>标签中的href属性
        for a_tag in soup.find_all('a', href=True):
            if not a_tag['href'].startswith('http'):
                a_tag['href'] = urljoin(base_url, a_tag['href'])
        
        # 提取下载链接
        download_links = [
            {'link': a_tag['href'], 'text': a_tag.get_text(strip=True).lower()}
            for a_tag in soup.find_all('a', href=True, target='_blank')
            if '.torrent' in a_tag.get_text(strip=True).lower() or 'download' in a_tag.get_text(strip=True).lower()
        ]
        
        return str(soup), download_links
    except requests.RequestException as e:
        logger.error(f"解析链接时发生请求异常: {e}")
        return None, []

def download_file(session, download_link, filename, selected_title, download_dir='/Torrent'):
    """下载文件到指定的下载目录"""
    # 确保下载目录存在，如果不存在则创建
    if not os.path.exists(download_dir):
        os.makedirs(download_dir)
        logger.info(f"创建下载目录: {download_dir}")

    # 构建完整的文件路径
    full_path = os.path.join(download_dir, filename)

    try:
        response = session.get(download_link, stream=True)
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

def download_tv_series(tv_info, formhash):
    """下载指定剧集，并尝试连续下载下一集"""
    title = tv_info['剧集']
    missing_episodes = set(tv_info['missing_episodes'])  # 使用集合来存储缺失的集数，方便后续操作
    current_episode = min(missing_episodes) if missing_episodes else 1
    formatted_episode_number = f'{"0" if current_episode < 10 else ""}{current_episode}'
    
    while missing_episodes:  # 只要还有缺失的集数就继续下载
        logger.info(f"正在搜索: {title} 第{formatted_episode_number}集")
        search_result_html = perform_search(session, search_url, formhash, title)  # 只使用标题进行搜索
        if not search_result_html:
            logger.warning(f"未找到 {title} 的搜索结果，跳过该电视节目")
            break

        parsed_results = parse_search_results(
            search_result_html, 
            title, 
            formatted_episode_number, 
            exclude_keywords, 
            preferred_resolution, 
            fallback_resolution
        )
        if not parsed_results:
            logger.warning(f"未找到 {title} 第{formatted_episode_number}集的任何匹配结果，跳过该电视节目")
            break

        # 选择最合适的资源（优先选择包含更多集数的资源）
        selected_result = max(parsed_results, key=lambda x: x['episode_range'][1] - x['episode_range'][0])
        start_episode, end_episode = selected_result['episode_range']
        
        logger.debug(f"选择链接: {selected_result['title']}, 文件大小: {selected_result['file_size']:.2f} GB")
        
        absolute_link = urljoin(base_url, selected_result['link'])
        parsed_link_content, download_links = get_and_parse_link(session, absolute_link, title, base_url)
        if parsed_link_content and download_links:
            chosen_download_link = download_links[0]
            logger.debug(f"选择下载链接: {chosen_download_link['link']}, 文件名: {chosen_download_link['text']}")
            if download_file(session, chosen_download_link['link'], chosen_download_link['text'], selected_result['title']):
                if start_episode == end_episode:
                    logger.info(f"{title} 第{start_episode}集下载成功")
                else:
                    logger.info(f"{title} 第{start_episode}集至第{end_episode}集下载成功")

                # 更新缺失集数列表，移除已下载的集数
                for ep in range(start_episode, end_episode + 1):
                    if ep in missing_episodes:
                        missing_episodes.remove(ep)

                # 如果还有缺失的集数，尝试下载下一集
                if missing_episodes:
                    current_episode = min(missing_episodes)
                    formatted_episode_number = f'{"0" if current_episode < 10 else ""}{current_episode}'
                else:
                    logger.info(f"{title} 全{end_episode}集下载成功")
                    break
            else:
                logger.error(f"{title} 第{formatted_episode_number}集下载失败")
                break
        else:
            logger.error("解析链接内容或下载链接失败")
            break

    if not missing_episodes:
        logger.info(f"{title} 所有缺失集数已下载完成")

def main():
    extractor = TVInfoExtractor(db_path, config)
    tv_info_list = extractor.extract_tv_info()

    if not load_and_check_cookies(session, user_profile_url):
        if not login(session, username, password):
            logger.error("登录失败，程序终止")
            return
    
    formhash = get_formhash(session, login_page_url)
    if not formhash:
        logger.error("无法继续，因为没有获取到formhash")
        return

    for tv_info in tv_info_list:
        download_tv_series(tv_info, formhash)

if __name__ == '__main__':
    main()