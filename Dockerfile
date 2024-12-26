# 使用 Python 3.9 作为基础镜像
FROM python:3.9-slim

# 设置工作目录
WORKDIR /app

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && rm requirements.txt

# 复制 Python 脚本
COPY tvshow_downloader.py .
COPY movie_downloader.py .
COPY actor_nfo.py .
COPY episodes_nfo.py .
COPY manual_search.py .
COPY settings.py .
COPY app.py .
COPY check_rss.py .
COPY rss.py .
COPY scan_media.py .
COPY sync.py .
COPY tmdb_id.py .

# 复制 HTML 模板
COPY templates.tar .
RUN tar -xvf templates.tar -C /app/ \
    && rm templates.tar

# 创建定时任务脚本
COPY main.py .

# 运行定时任务脚本
CMD ["python", "main.py"]

# 声明监听端口
EXPOSE 8888