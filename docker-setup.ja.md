# Docker セットアップ手順

[English](docker-setup.md) | **日本語**

## 構成

```
syncvey/
├── docker-compose.yml
├── Dockerfile
└── manage.py
```

### コンテナ一覧

| コンテナ名       | イメージ             | ポート      | 役割          |
|------------------|----------------------|-------------|---------------|
| `syncvey-app` | ローカルビルド        | 8000 → 8000 | Django アプリ |
| `syncvey-db`  | postgres:18.3-alpine | 5432 → 5432 | PostgreSQL    |

---

## 起動手順

### 初回・クリーンスタート

```bash
# イメージのビルドとコンテナ起動
docker compose up --build -d
```

起動時に以下が自動で実行されます：

1. `migrate` — テーブル作成
2. `createsuperuser` — 管理者アカウント作成（`.env` の値を使用）
3. `seed` — サンプルデータ投入

### 初回ログイン

アプリ（資産台帳）と Django 管理サイトでアカウントが分かれています。
**superuser（`admin` 等）は管理専用**で、アプリ画面にアクセスすると `/admin/` にリダイレクトされます。アプリは組織メンバーでログインしてください。

| 用途 | URL | Username | Password |
|------|-----|----------|----------|
| アプリ（資産台帳） | http://localhost:8000 | `tabata_hiroshi` | `Passw0rd!` |
| Django 管理サイト | http://localhost:8000/admin/ | `admin`（`.env` で変更可） | `admin` |

> シードは他にも役割の異なる組織メンバーを作成します（例: `tanaka_kenji`=インフラ管理, `yamada_yuki`=アプリ管理, `sato_mai`=閲覧者、いずれも `Passw0rd!`）。権限による表示差を試せます。

### 2回目以降

```bash
docker compose up -d
```

migrate・createsuperuser・seed はべき等なので毎回実行されますが、既存データは上書きされません。

---

## 停止・削除

```bash
# 停止（データは保持）
docker compose down

# 停止 + DB データも削除
docker compose down -v
```

---

## よく使うコマンド

### ログ確認

```bash
# 全コンテナ
docker compose logs -f

# 個別
docker compose logs -f app
docker compose logs -f db
```

### コンテナへの接続

```bash
# アプリ
docker exec -it syncvey-app bash

# DB（PostgreSQL）
docker exec -it syncvey-db psql -U user -d asset_manager
```

### マイグレーション（手動実行）

```bash
docker exec syncvey-app python manage.py makemigrations
docker exec syncvey-app python manage.py migrate
```

### パッケージの追加

```bash
# requirements.txt（本番）に追記後、イメージを再ビルド
docker compose build app
docker compose up -d app
```

### テスト用パッケージのインストール

```bash
# requirements-dev.txt（moto / pytest 等）をコンテナに追加インストール
docker compose exec app pip install -r requirements-dev.txt
```

---

## 環境変数（`.env`）

| 変数名                       | 説明                     | デフォルト値        |
|------------------------------|--------------------------|---------------------|
| `DB_NAME`                    | DB 名                    | `asset_manager`     |
| `DB_USER`                    | DB ユーザー              | `user`              |
| `DB_PASSWORD`                | DB パスワード            | `password`          |
| `DB_PORT_EXTERNAL`           | ホスト側 DB ポート       | `5432`              |
| `DATABASE_URL`               | 接続 URL                 | —                   |
| `DJANGO_SUPERUSER_USERNAME`  | 初期管理者ユーザー名     | `admin`             |
| `DJANGO_SUPERUSER_EMAIL`     | 初期管理者メール         | `admin@example.com` |
| `DJANGO_SUPERUSER_PASSWORD`  | 初期管理者パスワード     | `admin`             |
| `SECRET_KEY`                 | Django シークレットキー  | —（本番は必須）      |
| `AWS_ACCESS_KEY_ID`          | AWS アクセスキー         | —                   |
| `AWS_SECRET_ACCESS_KEY`      | AWS シークレットキー     | —                   |
| `AWS_SCAN_REGIONS`           | スキャン対象リージョン   | `ap-northeast-1,...`|
| `SLACK_WEBHOOK_URL`          | Slack通知（共通チャンネル）| —（任意）          |

---

## トラブルシューティング

### DB の起動を待たずにアプリが落ちる

`docker-compose.yml` の `healthcheck` が通るまでアプリは待機します。
それでも失敗する場合は手動で再起動してください。

```bash
docker compose restart app
```

### ポートが競合する

`.env` の `APP_PORT_EXTERNAL` または `DB_PORT_EXTERNAL` を変更してください。

```
APP_PORT_EXTERNAL=8001
DB_PORT_EXTERNAL=5433
```
