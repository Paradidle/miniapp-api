# 微信云托管镜像（低内存 Python 3.11 slim）
FROM python:3.11-slim

# 云托管要求监听 0.0.0.0:80；平台会注入 PORT，默认 80
ENV PORT=80 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 先单独拷贝依赖文件，利用构建缓存
COPY requirements.txt .
# 腾讯云构建环境在国内，使用清华镜像加速；如在境外构建可删掉 -i 参数
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

COPY . .

EXPOSE 80

# 云托管会将流量打到容器的 80 端口
CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]
