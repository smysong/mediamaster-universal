from urllib.parse import urljoin, urlparse, parse_qs, urlencode, unquote
import logging
import requests
import os
import re
import json
import configparser
from bs4 import BeautifulSoup

# 配置日志记录
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class MediaDownloader:
    def __init__(self):
        self.session = requests.Session()
        # 创建一个ConfigParser对象并读取配置文件
        self.config = configparser.ConfigParser()
        config_path = '/config/config.ini'
        if not os.path.exists(config_path):
            logger.error(f"配置文件 {config_path} 不存在")
            exit(1)
        self.config.read(config_path, encoding='utf-8')

        # 从配置文件中读取必要的配置项
        self.movie_base_url = self.config.get("urls", "movie_url", fallback="https://www.hdbthd.com")
        self.tvshow_base_url = self.config.get("urls", "tv_url", fallback="https://www.bthdtv.com")

        # 读取排除关键词和分辨率配置
        exclude_keywords_str = self.config.get("resources", "exclude_keywords", fallback="")
        self.exclude_keywords = [kw.strip().lower() for kw in exclude_keywords_str.split(',') if kw.strip()]
        self.preferred_resolution = self.config.get("resources", "preferred_resolution", fallback="")
        self.fallback_resolution = self.config.get("resources", "fallback_resolution", fallback="")

    @staticmethod
    def encode_form_data(form_data, encoding='gbk'):
        """将表单数据编码为指定的编码格式"""
        return {key: value.encode(encoding).decode('latin1') for key, value in form_data.items()}

    @staticmethod
    def preserve_encoding_in_redirect(response, *args, **kwargs):
        """在重定向前保持原始编码"""
        if response.is_redirect:
            redirect_url = response.headers.get('Location')
            if redirect_url:
                base_url = response.request.url
                redirect_url = urljoin(base_url, redirect_url)
                parsed_url = urlparse(redirect_url)
                query_params = parse_qs(parsed_url.query, encoding='latin1')

                site_type = kwargs.get('site_type', 'movie')  # 默认值为 'movie'
                encoding = 'gbk' if site_type == 'movie' else 'utf-8'
                
                if 'kw' in query_params:
                    query_params['kw'] = [param.encode('latin1').decode(encoding) for param in query_params['kw']]
                
                new_query_string = urlencode(query_params, doseq=True, encoding=encoding, errors='surrogateescape')
                final_redirect_url = parsed_url._replace(query=new_query_string).geturl()

                logger.debug(f"重定向到: {final_redirect_url}")
                response.headers['Location'] = final_redirect_url

    def perform_search(self, session, search_url, formhash, keyword, headers, site_type='movie', year=None):
        """执行搜索操作，只使用标题作为关键词"""
        form_data = {
            'formhash': formhash,
            'srchtxt': keyword,
            'searchsubmit': 'yes'
        }

        encoding = 'gbk' if site_type == 'movie' else 'utf-8'
        encoded_form_data = urlencode(form_data, encoding=encoding)

        logger.info(f"开始搜索： {keyword} ")

        search_response = session.post(
            search_url, 
            data=encoded_form_data, 
            headers=headers,
            allow_redirects=False,
            hooks={'response': lambda r, *a, **k: self.preserve_encoding_in_redirect(r, *a, site_type=site_type, **k)},
            timeout=10  # 设置超时时间
        )

        while search_response.is_redirect:
            redirect_url = search_response.headers.get('Location')
            if not redirect_url:
                logger.error("未找到重定向URL")
                return None

            base_url = search_response.request.url
            redirect_url = urljoin(base_url, redirect_url)

            search_response = session.get(
                redirect_url, 
                allow_redirects=False, 
                hooks={'response': lambda r, *a, **k: self.preserve_encoding_in_redirect(r, *a, site_type=site_type, **k)},
                timeout=10  # 设置超时时间
            )

        if search_response.status_code == 200:
            search_response.encoding = encoding
            html_content = search_response.text
            return html_content
        else:
            logger.error(f"最终请求失败，状态码: {search_response.status_code}")
            return None

    def search_media(self, session, keyword, year=None, site_type='movie'):
        """通用搜索方法"""
        base_url = self.movie_base_url if site_type == 'movie' else self.tvshow_base_url
        user_profile_url = f"{base_url}/home.php?mod=space"

        # 检查并更新登录状态
        if not self.load_and_check_cookies(session, user_profile_url, site_type):
            if not self.login(session, self.config.get("resources", "login_username"), self.config.get("resources", "login_password"), base_url, site_type):
                logger.error(f"{site_type.capitalize()}站点登录失败，无法继续操作")
                return []

        search_url = f"{base_url}/search.php?mod=forum"
        headers = self.get_headers(base_url)
        formhash = self.get_formhash_for_search(session, search_url)
        if not formhash:
            logger.error(f"无法获取{site_type.capitalize()}站点formhash，无法继续操作")
            return []

        html_content = self.perform_search(session, search_url, formhash, keyword, headers, site_type=site_type, year=year)
        if html_content:
            results = self.parse_search_results(html_content, keyword, year, self.exclude_keywords, self.preferred_resolution, self.fallback_resolution, site_type)
            logger.debug(f"解析到的搜索结果: {results}")
            return results
        else:
            logger.error("搜索结果为空")
            return []

    def search_movie(self, session, keyword, year=None):
        """搜索电影"""
        return self.search_media(session, keyword, year, site_type='movie')

    def search_tvshow(self, session, keyword, year=None):
        """搜索电视剧"""
        return self.search_media(session, keyword, year, site_type='tvshow')

    def load_and_check_cookies(self, session, user_profile_url, site_type):
        """加载并检查现有的cookies"""
        cookie_file = f'/tmp/{site_type}_cookies.json'
        if os.path.exists(cookie_file):
            with open(cookie_file, 'r') as file:
                cookies_dict = json.load(file)
                session.cookies.update(cookies_dict)
                response = session.get(user_profile_url)
                if response.status_code == 200 and self.config.get("resources", "login_username", fallback="") in response.text:
                    logger.debug("Cookies有效，无需重新登录")
                    return True
                else:
                    logger.warning("Cookies无效，需要重新登录")
                    return False
        else:
            logger.info("未找到现有cookies，需要重新登录")
            return False

    def login(self, session, username, password, base_url, site_type):
        """执行登录操作"""
        login_page_url = f"{base_url}/member.php?mod=logging&action=login"
        login_url = f"{base_url}/member.php?mod=logging&action=login&loginsubmit=yes&inajax=1"

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
            logger.debug("登录成功")
            cookie_file = f'/tmp/{site_type}_cookies.json'
            with open(cookie_file, 'w') as file:
                json.dump(requests.utils.dict_from_cookiejar(session.cookies), file)
            return True
        else:
            logger.error(f"登录失败，状态码: {login_response.status_code}")
            return False

    def get_formhash_for_search(self, session, url):
        """为搜索请求获取formhash"""
        try:
            response = session.get(url, timeout=10)
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

    @staticmethod
    def should_exclude(title, exclude_keywords):
        """检查标题是否包含排除关键词"""
        return any(keyword.lower() in title.lower() for keyword in exclude_keywords)

    @staticmethod
    def extract_year(title):
        """从标题中提取年份"""
        match = re.search(r'\b(\d{4})\b', title)
        return int(match.group(1)) if match else None

    def parse_search_results(self, html_content, title, year, exclude_keywords, preferred_resolution, fallback_resolution, site_type):
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
                
                if year and str(year) not in result_title:
                    continue
                
                if title.lower() not in result_title.lower() or self.should_exclude(result_title, exclude_keywords):
                    continue
                
                result_year = self.extract_year(result_title) if year else None

                # 根据站点类型补充完整URL
                base_url = self.movie_base_url if site_type == 'movie' else self.tvshow_base_url
                if not link.startswith(('http://', 'https://')):
                    link = urljoin(base_url, link)

                results.append({
                    'title': result_title,
                    'link': link,
                    'year': result_year
                })

            preferred_results = []
            fallback_results = []
            for result in results:
                if preferred_resolution and preferred_resolution.lower() in result['title'].lower():
                    preferred_results.append(result)
                elif fallback_resolution and fallback_resolution.lower() in result['title'].lower():
                    fallback_results.append(result)
            
            all_results = preferred_results + fallback_results
            if all_results:
                return all_results
            else:
                logger.info(f"未匹配到任何分辨率结果")
                return []
        except Exception as e:
            logger.error(f"解析搜索结果时发生异常: {e}")
            return []

    def is_logged_in(self, session, user_profile_url):
        """检查当前会话是否已登录"""
        try:
            response = session.get(user_profile_url)
            if response.status_code == 200 and self.config.get("resources", "login_username", fallback="") in response.text:
                return True
            else:
                return False
        except requests.RequestException as e:
            logger.error(f"检查登录状态时发生请求异常: {e}")
            return False

    def get_and_parse_link(self, session, link, title, site_type):
        """发送GET请求并解析选定链接的内容，确保所有链接都是完整URL，并提取下载链接"""
        try:
            base_url = self.movie_base_url if site_type == 'movie' else self.tvshow_base_url
            user_profile_url = f"{base_url}/home.php?mod=space"
     
            if not self.is_logged_in(session, user_profile_url):
                if not self.login(session, self.config.get("resources", "login_username"), self.config.get("resources", "login_password"), base_url, site_type):
                    logger.error(f"{site_type}站点登录失败，无法继续操作")
                    return None, []

            if not link.startswith(('http://', 'https://')):
                link = urljoin(base_url, link)

            response = session.get(link)
            if response.status_code != 200:
                logger.error(f"解析链接失败，状态码: {response.status_code}")
                return None, []
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            for a_tag in soup.find_all('a', href=True):
                if not a_tag['href'].startswith(('http://', 'https://')):
                    a_tag['href'] = urljoin(base_url, a_tag['href'])
            
            download_links = []
            for a_tag in soup.select('span[id^="attach_"] a[href]'):
                href = a_tag['href']
                text = a_tag.get_text(strip=True).lower()
                
                if '.torrent' in text or '.torrent' in href:
                    filename_match = re.search(r'(.*?\.torrent)', text)
                    if filename_match:
                        filename = filename_match.group(1)
                    else:
                        filename = unquote(href.split('/')[-1].split('?')[0])
                    
                    full_link = urljoin(base_url, href)
                    download_links.append({'link': full_link, 'text': text, 'filename': filename})
                    logger.debug(f"找到下载链接: {title}, 链接: {full_link}, 文件名：{filename}")

            if not download_links:
                logger.warning(f"未找到下载链接: {title}")
            
            return str(soup), download_links
        except requests.RequestException as e:
            logger.error(f"解析链接时发生请求异常: {e}")
            return None, []

    def download_file(self, session, download_links, download_dir='/Torrent'):
        """下载文件到指定的下载目录"""
        try:
            parsed_url = urlparse(download_links[0]['link'])
            base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
            user_profile_url = f"{base_url}/home.php?mod=space"

            if not self.is_logged_in(session, user_profile_url):
                site_type = 'movie' if 'movie' in base_url else 'tvshow'
                if not self.login(session, self.config.get("resources", "login_username"), self.config.get("resources", "login_password"), base_url, site_type):
                    logger.error(f"{site_type}站点登录失败，无法继续操作")
                    return False

            if not os.path.exists(download_dir):
                os.makedirs(download_dir)

            full_path = os.path.join(download_dir, download_links[0]['filename'])

            response = session.get(download_links[0]['link'], stream=True)
            if response.status_code != 200:
                logger.error(f"下载文件失败，状态码: {response.status_code}")
                return False
            
            with open(full_path, 'wb') as file:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        file.write(chunk)
            logger.debug(f"文件下载成功: {full_path}")
            return True
        except requests.RequestException as e:
            logger.error(f"下载文件时发生请求异常: {e}")
            return False

    def download_media(self, session, link, title, year, site_type):
        """通用下载方法"""
        logger.debug(f"开始下载{site_type}: {title} ({year})")
        
        if not link.startswith(('http://', 'https://')):
            base_url = self.movie_base_url if site_type == 'movie' else self.tvshow_base_url
            link = urljoin(base_url, link)

        html_content, download_links = self.get_and_parse_link(session, link, title, site_type)
        if not download_links:
            logger.error(f"未找到可用的下载链接: {link}")
            return False
        
        for download_link_info in download_links:
            download_link = download_link_info['link']
            if not self.download_file(session, [download_link_info]):
                logger.error(f"下载文件失败: {download_link}")
                return False
        
        logger.info(f"{site_type.capitalize()}下载完成: {title} ({year})")
        return True

    def download_movie(self, session, link, title, year):
        """下载电影"""
        return self.download_media(session, link, title, year, site_type='movie')
    
    def download_tvshow(self, session, link, title, year):
        """下载电视剧"""
        link = urljoin(self.tvshow_base_url, link)
        return self.download_media(session, link, title, year, site_type='tvshow')

    def get_headers(self, base_url):
        """获取通用请求头"""
        return {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Origin': base_url,
            'Connection': 'keep-alive',
            'Referer': base_url,
            'Accept-Encoding': 'gzip, deflate, br'
        }

    def run(self):
        pass

if __name__ == "__main__":
    downloader = MediaDownloader()
    downloader.run()