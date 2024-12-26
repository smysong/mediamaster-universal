import os
import re
import sqlite3
import configparser
import logging

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s', encoding='utf-8')

def read_config(file_path):
    config = configparser.ConfigParser()
    config.read(file_path, encoding='utf-8')
    return config

def scan_directory(path):
    movies = []
    episodes = {}

    for root, dirs, files in os.walk(path):
        for file in files:
            # 将文件扩展名转换为小写
            if file.lower().endswith(('.mkv', '.mp4')):
                # 匹配电影文件名模式
                movie_match = re.match(r'^(.*) - \((\d{4})\) (\d+p)\.(mkv|mp4)$', file, re.IGNORECASE)
                if movie_match:
                    movie_name = movie_match.group(1).strip()
                    year = movie_match.group(2)
                    movies.append((movie_name, year))
                    continue

                # 匹配电视剧文件名模式
                episode_match = re.match(r'^(.*) - S(\d+)E(\d+) - (.*)\.(mkv|mp4)$', file, re.IGNORECASE)
                if episode_match:
                    show_name = episode_match.group(1).strip()
                    season = int(episode_match.group(2))
                    episode = int(episode_match.group(3))

                    if show_name not in episodes:
                        episodes[show_name] = {}
                    if season not in episodes[show_name]:
                        episodes[show_name][season] = []

                    if episode not in episodes[show_name][season]:
                        episodes[show_name][season].append(episode)

    return movies, episodes

def create_database(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 创建 LIB_MOVIES 表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS LIB_MOVIES (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        year INTEGER NOT NULL,
        tmdb_id TEXT 
    )
    ''')

    # 创建 LIB_TVS 表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS LIB_TVS (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL UNIQUE,
        year INTEGER,
        tmdb_id TEXT 
    )
    ''')

    # 创建 LIB_TV_SEASONS 表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS LIB_TV_SEASONS (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tv_id INTEGER NOT NULL,
        season INTEGER NOT NULL,
        episodes TEXT NOT NULL,
        FOREIGN KEY (tv_id) REFERENCES LIB_TVS (id)
    )
    ''')

    conn.commit()
    conn.close()
    logging.info("数据库和表创建成功。")

def insert_or_update_movies(db_path, movies):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    for title, year in movies:
        cursor.execute('''
        SELECT id FROM LIB_MOVIES WHERE title = ? AND year = ?
        ''', (title, year))
        existing_movie = cursor.fetchone()
        if existing_movie:
            logging.debug(f"电影 '{title} ({year})' 已存在于数据库中。")
        else:
            cursor.execute('''
            INSERT INTO LIB_MOVIES (title, year) VALUES (?, ?)
            ''', (title, year))
            logging.info(f"已将电影 '{title} ({year})' 插入数据库。")

    conn.commit()
    conn.close()

def insert_or_update_episodes(db_path, episodes):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    for show_name, seasons in episodes.items():
        cursor.execute('''
        SELECT id FROM LIB_TVS WHERE title = ?
        ''', (show_name,))
        existing_tv = cursor.fetchone()
        if existing_tv:
            tv_id = existing_tv[0]
            logging.debug(f"电视剧 '{show_name}' 已存在于数据库中。")
        else:
            cursor.execute('''
            INSERT INTO LIB_TVS (title) VALUES (?)
            ''', (show_name,))
            tv_id = cursor.lastrowid
            logging.info(f"已将电视剧 '{show_name}' 插入数据库。")

        for season, episodes in seasons.items():
            episodes_str = ','.join(map(str, sorted(episodes)))

            cursor.execute('''
            SELECT id, episodes FROM LIB_TV_SEASONS WHERE tv_id = ? AND season = ?
            ''', (tv_id, season))
            existing_season = cursor.fetchone()

            if existing_season:
                existing_episodes_str = existing_season[1]
                existing_episodes = set(map(int, existing_episodes_str.split(',')))
                new_episodes = set(episodes)

                if new_episodes != existing_episodes:
                    updated_episodes_str = ','.join(map(str, sorted(new_episodes.union(existing_episodes))))
                    cursor.execute('''
                    UPDATE LIB_TV_SEASONS SET episodes = ? WHERE id = ?
                    ''', (updated_episodes_str, existing_season[0]))
                    logging.info(f"已更新电视剧 '{show_name}' 第 {season} 季的集数：{updated_episodes_str}")
                else:
                    logging.debug(f"电视剧 '{show_name}' 第 {season} 季已是最新状态。")
            else:
                cursor.execute('''
                INSERT INTO LIB_TV_SEASONS (tv_id, season, episodes) VALUES (?, ?, ?)
                ''', (tv_id, season, episodes_str))
                logging.info(f"已将电视剧 '{show_name}' 第 {season} 季的集数 {episodes_str} 插入数据库。")

    conn.commit()
    conn.close()

def delete_obsolete_movies(db_path, current_movies):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute('SELECT title, year FROM LIB_MOVIES')
    all_movies = cursor.fetchall()

    for title, year in all_movies:
        if (title, str(year)) not in current_movies:
            cursor.execute('DELETE FROM LIB_MOVIES WHERE title = ? AND year = ?', (title, year))
            logging.info(f"已从数据库中删除电影 '{title} ({year})'。")

    conn.commit()
    conn.close()

def delete_obsolete_episodes(db_path, current_episodes):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute('SELECT id, title FROM LIB_TVS')
    all_shows = cursor.fetchall()

    for tv_id, title in all_shows:
        if title not in current_episodes:
            cursor.execute('DELETE FROM LIB_TV_SEASONS WHERE tv_id = ?', (tv_id,))
            cursor.execute('DELETE FROM LIB_TVS WHERE id = ?', (tv_id,))
            logging.info(f"已从数据库中删除电视剧 '{title}' 及其所有季。")
        else:
            cursor.execute('SELECT season, episodes FROM LIB_TV_SEASONS WHERE tv_id = ?', (tv_id,))
            all_seasons = cursor.fetchall()

            for season, episodes_str in all_seasons:
                existing_episodes = set(map(int, episodes_str.split(',')))
                current_episodes_set = set(current_episodes[title].get(season, []))

                if not current_episodes_set.issubset(existing_episodes):
                    cursor.execute('DELETE FROM LIB_TV_SEASONS WHERE tv_id = ? AND season = ?', (tv_id, season))
                    logging.info(f"已从数据库中删除电视剧 '{title}' 第 {season} 季。")

    conn.commit()
    conn.close()

def update_tv_year(episodes_path, db_path):
    # 正则表达式用于匹配电视剧标题和年份
    pattern = re.compile(r'^(.*)\s+\((\d{4})\)')

    def scan_directories(path):
        # 获取所有文件夹名称
        directories = [name for name in os.listdir(path) if os.path.isdir(os.path.join(path, name))]
        
        # 解析每个文件夹名称
        shows = []
        for directory in directories:
            match = pattern.match(directory)
            if match:
                title = match.group(1).strip()
                year = int(match.group(2))
                shows.append({'title': title, 'year': year})
        
        return shows

    def update_database(db_path, shows):
        # 连接到数据库
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 更新数据库中的记录
        for show in shows:
            title = show['title']
            year = show['year']
            
            # 查询数据库中是否存在相同的标题和年份
            cursor.execute("SELECT id FROM LIB_TVS WHERE title = ? AND year = ?", (title, year))
            result = cursor.fetchone()
            
            if result:
                logging.debug(f"已存在相同数据，跳过更新：{title} ({year})")
            else:
                # 查询数据库中是否存在相同的标题
                cursor.execute("SELECT id FROM LIB_TVS WHERE title = ?", (title,))
                result = cursor.fetchone()
                
                if result:
                    show_id = result[0]
                    # 更新年份
                    cursor.execute("UPDATE LIB_TVS SET year = ? WHERE id = ?", (year, show_id))
                    logging.info(f"更新 {title} 的年份：{year}")
                else:
                    logging.warning(f"没有匹配条目：{title}")

        # 提交并关闭数据库连接
        conn.commit()
        conn.close()

    # 扫描目录并提取信息
    shows = scan_directories(episodes_path)
    
    # 更新数据库
    update_database(db_path, shows)

def main():
    config = read_config('/config/config.ini')  # 配置文件路径
    db_path = config['database']['db_path']
    movies_path = config['mediadir']['movies_path']
    episodes_path = config['mediadir']['episodes_path']

    # 创建数据库和表
    create_database(db_path)

    # 扫描目录
    movies, episodes = scan_directory(movies_path)
    _, more_episodes = scan_directory(episodes_path)

    # 合并电视剧结果
    for show, seasons in more_episodes.items():
        if show in episodes:
            for season, eps in seasons.items():
                if season in episodes[show]:
                    episodes[show][season].extend(eps)
                else:
                    episodes[show][season] = eps
        else:
            episodes[show] = seasons

    # 插入或更新电影数据
    insert_or_update_movies(db_path, movies)

    # 插入或更新电视剧数据
    insert_or_update_episodes(db_path, episodes)
    update_tv_year(episodes_path, db_path)

    # 删除数据库中多余的电影记录
    delete_obsolete_movies(db_path, movies)

    # 删除数据库中多余的电视剧记录
    delete_obsolete_episodes(db_path, episodes)

if __name__ == "__main__":
    main()