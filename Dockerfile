# ベースイメージを指定
FROM python:3.12-slim

# 環境変数を設定
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# 作業ディレクトリを設定
WORKDIR /app

# requirements.txt をコピーしてライブラリをインストール
# キャッシュをクリアし、意図しないmysqlclientをアンインストール
RUN apt-get update -qq && apt-get install -y --no-install-recommends \
        gettext \
        graphviz \
    && rm -rf /var/lib/apt/lists/*

RUN pip cache purge && \
    pip uninstall -y mysqlclient || true
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 開発環境では requirements-dev.txt を別途インストール
# docker compose exec app pip install -r requirements-dev.txt