import os
import re
import logging
import requests
import configparser
import shutil
import time
import subprocess
from collections import defaultdict
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# 定义常量
LOG_FILE_PATH = '/tmp/sync.log'
FILES_RECORD_PATH = '/config/files_record.txt'

# 清空日志文件
if os.path.exists(LOG_FILE_PATH):
    os.remove(LOG_FILE_PATH)

# 配置日志记录
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# 创建文件处理器
file_handler = logging.FileHandler(LOG_FILE_PATH)
file_handler.setLevel(logging.INFO)
file_formatter = logging.Formatter('%(levelname)s - %(message)s')
file_handler.setFormatter(file_formatter)

# 创建流处理器
stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.INFO)
stream_formatter = logging.Formatter('%(levelname)s - %(message)s')
stream_handler.setFormatter(stream_formatter)

# 添加处理器到日志记录器
logger.addHandler(file_handler)
logger.addHandler(stream_handler)

# 创建一个默认字典来存储缓存数据
cache = defaultdict(dict)

def read_config():
    config = configparser.ConfigParser()
    config.read('/config/config.ini')
    return config

def get_tmdb_info(title, year, media_type):
    try:
        # 检查缓存中是否有数据
        if (title, year) in cache[media_type]:
            return cache[media_type][(title, year)]
        
        config = read_config()
        TMDB_API_KEY = config['tmdb']['api_key']
        TMDB_BASE_URL = config['tmdb']['base_url']
        url = f"{TMDB_BASE_URL}/search/{media_type}"
        params = {
            'api_key': TMDB_API_KEY,
            'query': title,
            'language': 'zh-CN',
            'include_adult': 'false'
        }
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        search_results = response.json().get('results', [])
        for result in search_results:
            if media_type == 'movie' and str(result.get('release_date', '')).startswith(str(year)):
                # 将结果存入缓存
                cache[media_type][(title, year)] = (result['id'], result.get('title', ''))
                return result['id'], result.get('title', '')
            elif media_type == 'tv' and result.get('first_air_date', '').startswith(str(year)):
                # 将结果存入缓存
                cache[media_type][(title, year)] = (result['id'], result.get('name', ''))
                return result['id'], result.get('name', '')
    except requests.RequestException as e:
        logger.error(f"请求错误: {e}")
    return None, None

def get_tv_episode_name(tmdb_id, season_number, episode_number):
    try:
        config = read_config()
        TMDB_API_KEY = config['tmdb']['api_key']
        TMDB_BASE_URL = config['tmdb']['base_url']
        url = f"{TMDB_BASE_URL}/tv/{tmdb_id}/season/{season_number}/episode/{episode_number}"
        params = {
            'api_key': TMDB_API_KEY,
            'language': 'zh-CN'
        }
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        episode_info = response.json()
        return episode_info.get('name', f"第{episode_number}集")
    except requests.RequestException as e:
        logger.error(f"请求错误: {e}")
    return f"第{episode_number}集"

def extract_info(filename, folder_name=None):
    def extract_movie_info(filename, folder_name=None):
        # 文件名中的中文名称模式
        chinese_name_pattern_filename = r'([\u4e00-\u9fa5A-Za-z0-9：]+)(?=\.)'
        # 文件夹名称中的中文名称模式
        chinese_name_pattern_folder = r'】([\u4e00-\u9fa5A-Za-z0-9：$$(). ]+)'
        english_name_pattern = r'([A-Za-z0-9\.\s]+)(?=\.\d{4}(?:\.|$))'
        year_pattern = r'(\d{4})(?=\.|$)'
        quality_pattern = r'(\d{1,4}[pPkK])'
        suffix_pattern = r'\.(\w+)$'

        # 尝试从文件名中提取中文名称
        chinese_name = re.search(chinese_name_pattern_filename, filename)
        if chinese_name and re.search(r'[\u4e00-\u9fa5]', chinese_name.group(1)):
            chinese_name = chinese_name.group(1)
        else:
            chinese_name = None

        # 如果文件名中未找到中文名称且提供了文件夹名称，则尝试从文件夹名称中提取
        if not chinese_name and folder_name:
            chinese_name = re.search(chinese_name_pattern_folder, folder_name)
            if chinese_name and re.search(r'[\u4e00-\u9fa5]', chinese_name.group(1)):
                chinese_name = chinese_name.group(1)
            else:
                chinese_name = None

        # 提取英文名称
        english_name = re.search(english_name_pattern, filename)
        english_name = english_name.group(1).strip() if english_name else None
        if english_name:
            english_name = english_name.replace('.', ' ').replace('-', ' ')

        # 提取年份
        year = re.search(year_pattern, filename)
        year = year.group() if year else None

        # 提取视频质量
        quality = re.search(quality_pattern, filename)
        quality = quality.group().upper() if quality else None

        # 提取文件后缀名
        suffix = re.search(suffix_pattern, filename)
        suffix = suffix.group(1) if suffix else None

        # 构建结果字典
        result = {
            '名称': chinese_name if chinese_name else english_name,
            '发行年份': year,
            '视频质量': quality,
            '后缀名': suffix
        }

        # 如果从文件名中未能提取到年份信息，从文件夹名称中尝试提取
        if not year and folder_name:
            folder_year = re.search(year_pattern, folder_name)
            if folder_year:
                result['发行年份'] = folder_year.group()

        return result

    def extract_tv_info(filename, folder_name=None):
        # 文件名中的中文名称模式
        chinese_name_pattern_filename = r'([\u4e00-\u9fa5A-Za-z0-9：]+)(?=\.)'
        # 文件夹名称中的中文名称模式
        chinese_name_pattern_folder = r'】([\u4e00-\u9fa5A-Za-z0-9：$$(). ]+)'
        english_name_pattern = r'([A-Za-z0-9\.\s]+)(?=\.(?:S\d{1,2}|E\d{1,2}|EP\d{1,2}))'
        season_pattern = r'S(\d{1,2})'
        episode_pattern = r'(?:E|EP)(\d{1,2})'
        year_pattern = r'(\d{4})'
        quality_pattern = r'(\d{1,4}[pPkK])'
        suffix_pattern = r'\.(\w+)$'

        # 尝试从文件名中提取中文名称
        chinese_name = re.search(chinese_name_pattern_filename, filename)
        if chinese_name and re.search(r'[\u4e00-\u9fa5]', chinese_name.group(1)):
            chinese_name = chinese_name.group(1)
        else:
            chinese_name = None

        # 如果文件名中未找到中文名称且提供了文件夹名称，则尝试从文件夹名称中提取
        if not chinese_name and folder_name:
            chinese_name = re.search(chinese_name_pattern_folder, folder_name)
            if chinese_name and re.search(r'[\u4e00-\u9fa5]', chinese_name.group(1)):
                chinese_name = chinese_name.group(1)
            else:
                chinese_name = None

        # 提取英文名称
        english_name = re.search(english_name_pattern, filename)
        english_name = english_name.group(1).strip() if english_name else None
        if english_name:
            english_name = english_name.replace('.', ' ').replace('-', ' ')

        # 提取季数
        season = re.search(season_pattern, filename)
        season_number = season.group(1) if season else None

        # 提取集数
        episode = re.search(episode_pattern, filename)
        episode_number = episode.group(1) if episode else None

        # 提取年份
        year = re.search(year_pattern, filename)
        year = year.group() if year else None

        # 提取视频质量
        quality = re.search(quality_pattern, filename)
        quality = quality.group().upper() if quality else None

        # 提取文件后缀名
        suffix = re.search(suffix_pattern, filename)
        suffix = suffix.group(1) if suffix else None

        # 构建结果字典
        result = {
            '名称': chinese_name if chinese_name else english_name,
            '发行年份': year,
            '视频质量': quality,
            '后缀名': suffix
        }

        # 如果有集数信息，添加季数（如果没有的话默认为01）和集数
        if episode_number:
            if not season_number:
                season_number = '01'  # 如果没有季信息，默认使用01季
            result.update({
                '季': season_number,
                '集': episode_number
            })

        # 如果从文件名中未能提取到年份信息，从文件夹名称中尝试提取
        if not year and folder_name:
            folder_year = re.search(year_pattern, folder_name)
            if folder_year:
                result['发行年份'] = folder_year.group()

        return result

    # 判断是电影还是电视剧
    is_tv = re.search(r'(?:S\d{1,2}|E\d{1,2}|EP\d{1,2})', filename)
    if is_tv:
        return extract_tv_info(filename, folder_name)
    else:
        return extract_movie_info(filename, folder_name)

def move_or_copy_file(src, dst, action):
    try:
        if action == 'move':
            shutil.move(src, dst)
            logger.info(f"文件已移动: {src} -> {dst}")
        elif action == 'copy':
            shutil.copy2(src, dst)
            logger.info(f"文件已复制: {src} -> {dst}")
        else:
            logger.error(f"未知操作: {action}")
    except Exception as e:
        logger.error(f"文件操作失败: {e}")

def is_common_video_file(filename):
    common_video_extensions = ['.mkv', '.mp4', '.avi', '.mov']
    extension = os.path.splitext(filename)[1].lower()
    return extension in common_video_extensions

def is_unfinished_download_file(filename):
    unfinished_extensions = ['.xltd', '.!qB', '.part']
    extension = os.path.splitext(filename)[1].lower()
    return extension in unfinished_extensions

def load_processed_files():
    processed_filenames = set()
    if os.path.exists(FILES_RECORD_PATH):
        with open(FILES_RECORD_PATH, 'r') as f:
            for line in f.read().splitlines():
                processed_filenames.add(line.split('/')[-1])
    return processed_filenames

def save_processed_files(processed_filenames):
    with open(FILES_RECORD_PATH, 'w') as f:
        for filename in processed_filenames:
            f.write(filename + '\n')

def refresh_media_library():
    # 刷新媒体库
    subprocess.run(['python', 'scan_media.py'])  
    # 刷新正在订阅
    subprocess.run(['python', 'check_rss.py'])   
    # 刷新媒体库tmdb_id
    subprocess.run(['python', 'tmdb_id.py'])

def process_file(file_path, processed_filenames):
    try:
        config = read_config()
        excluded_filenames = config['downloadtransfer']['excluded_filenames'].split(',')
        action = config['downloadtransfer']['action']
        movie_directory = config['mediadir']['movies_path']
        episode_directory = config['mediadir']['episodes_path']

        filename = os.path.basename(file_path)
        folder_name = os.path.basename(os.path.dirname(file_path))

        if not is_common_video_file(filename) and is_unfinished_download_file(filename):
            logger.debug(f"跳过下载未完成文件：{file_path}")
            return

        extension = os.path.splitext(filename)[1].lower()
        if filename in excluded_filenames:
            logger.debug(f"跳过文件（文件名在排除列表中）: {file_path}")
            return
        if '【更多' in filename:
            logger.debug(f"跳过文件（包含特定字符）: {file_path}")
            return

        result = extract_info(filename, folder_name)
        if result:
            logger.info(f"文件名: {filename}")
            logger.info(f"解析结果: {result}")

            media_type = 'tv' if '季' in result and '集' in result else 'movie'
            target_directory = episode_directory if media_type == 'tv' else movie_directory

            tmdb_id, tmdb_name = get_tmdb_info(result['名称'], result['发行年份'], media_type)
            if tmdb_id:
                logger.info(f"获取到 TMDB ID: {tmdb_id}，名称：{tmdb_name}")

                title = tmdb_name if tmdb_name else result['名称']
                year = result['发行年份']
                target_base_dir = os.path.join(target_directory, f"{title} ({year})")

                if not os.path.exists(target_base_dir):
                    os.makedirs(target_base_dir)
                    logger.info(f"创建目录: {target_base_dir}")

                if media_type == 'tv':
                    season_number = result['季']
                    episode_number = result['集']
                    season_dir = os.path.join(target_base_dir, f"Season {int(season_number)}")
                    if not os.path.exists(season_dir):
                        os.makedirs(season_dir)
                        logger.info(f"创建目录: {season_dir}")

                    episode_name = get_tv_episode_name(tmdb_id, season_number, episode_number)
                    new_filename = f"{title} - S{season_number}E{episode_number.zfill(2)} - {episode_name}.{result['后缀名']}"
                    target_file_path = os.path.join(season_dir, new_filename)
                else:
                    new_filename = f"{title} - ({year}) {result['视频质量']}.{result['后缀名']}"
                    target_file_path = os.path.join(target_base_dir, new_filename)

                if filename in processed_filenames:
                    logger.debug(f"文件已处理，跳过: {filename}")
                    return

                move_or_copy_file(file_path, target_file_path, action)
                processed_filenames.add(filename)

                video_dir = os.path.dirname(file_path)
                nfo_filename = os.path.splitext(filename)[0] + '.nfo'
                nfo_file_path = os.path.join(video_dir, nfo_filename)
                if os.path.exists(nfo_file_path):
                    new_nfo_filename = f"{title} - S{season_number}E{episode_number.zfill(2)} - {episode_name}.nfo" if media_type == 'tv' else f"{title} - ({year}) {result['视频质量']}.nfo"
                    nfo_target_path = os.path.join(target_base_dir if media_type == 'movie' else season_dir, new_nfo_filename)
                    move_or_copy_file(nfo_file_path, nfo_target_path, action)
                    logger.info(f"转移NFO文件: {nfo_file_path} -> {nfo_target_path}")

                logger.info(f"文件处理完成，刷新本地数据库")
                refresh_media_library()

                # 保存已处理的文件列表
                save_processed_files(processed_filenames)
            else:
                logger.warning(f"未能获取到 TMDB ID: {result['名称']} ({result['发行年份']})")
        else:
            logger.warning(f"无法解析文件名: {filename}")
    except Exception as e:
        logger.error(f"处理文件时发生错误: {file_path}, 错误: {e}")

class CustomFileHandler(FileSystemEventHandler):
    def __init__(self):
        self.original_filenames = {}
        self.unfinished_files = set()
        self.processed_files = load_processed_files()

    def on_created(self, event):
        if event.is_directory:
            return
        file_path = event.src_path
        filename = os.path.basename(file_path)
        if is_unfinished_download_file(filename):
            self.unfinished_files.add(file_path)
            logger.debug(f"发现下载未完成文件: {file_path}，开始监控")
        else:
            logger.debug(f"新文件创建: {file_path}")
            process_file(file_path, self.processed_files)

    def on_modified(self, event):
        if event.is_directory:
            return
        file_path = event.src_path
        filename = os.path.basename(file_path)
        if file_path in self.unfinished_files:
            if not is_unfinished_download_file(filename):
                self.unfinished_files.remove(file_path)
                logger.info(f"下载文件已完成: {file_path}，开始处理")
                process_file(file_path, self.processed_files)
        else:
            logger.debug(f"文件修改: {file_path}")
            if filename not in self.processed_files:
                process_file(file_path, self.processed_files)
            else:
                logger.debug(f"文件已处理，跳过: {filename}")

    def on_moved(self, event):
        if event.is_directory:
            return
        old_file_path = event.src_path
        new_file_path = event.dest_path
        logger.debug(f"文件重命名: {old_file_path} -> {new_file_path}")
        if os.path.basename(new_file_path) not in self.processed_files:
            process_file(new_file_path, self.processed_files)
        else:
            logger.debug(f"文件已处理，跳过: {os.path.basename(new_file_path)}")

def start_monitoring(directory):
    logger.info(f"开始监控目录: {directory}")
    event_handler = CustomFileHandler()
    observer = Observer()
    observer.schedule(event_handler, directory, recursive=True)
    observer.start()
    try:
        # 处理已存在的文件
        for root, dirs, files in os.walk(directory):
            for file in files:
                file_path = os.path.join(root, file)
                if is_common_video_file(file_path) or is_unfinished_download_file(file_path):
                    filename = os.path.basename(file_path)
                    if filename not in event_handler.processed_files:
                        process_file(file_path, event_handler.processed_files)
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
    logger.info("实时监控已停止")

if __name__ == "__main__":
    config = read_config()
    directory = config['downloadtransfer']['directory']
    start_monitoring(directory)