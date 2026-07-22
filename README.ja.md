# SyncVey

**[English](README.md)** | 日本語

**Terraform ドリフト検知付き AWS 資産台帳をワンストップで — セルフホスト・SaaS 料金なし。**

SyncVey は AWS リソースを **システム → 環境 → 資産** の階層で整理し、tfstate と AWS の実態の
構成ドリフトを検出し、アプリケーション情報（言語・フレームワーク・依存パッケージ）を環境別に
追跡します。`docker compose up` 一発で起動。

> 機能紹介ページ → **[https://mr-tabata.github.io/SyncVey/](https://mr-tabata.github.io/SyncVey/)**

---

## スクリーンショット

**ダッシュボード** — システム・環境・資産を一望

![SyncVey ダッシュボード](docs/screenshots/dashboard-ja.png)

**Drift レポート** — tfstate と AWS 実態の属性レベルの差分

![SyncVey Drift レポート](docs/screenshots/drift-ja.png)

**Drift 履歴** — ドリフトの推移と、各スナップショットのフィールド単位の差分

![SyncVey Drift 履歴](docs/screenshots/drift-history-ja.png)

![SyncVey Drift スナップショット詳細](docs/screenshots/drift-snapshot-ja.png)

---

## なぜ SyncVey を作ったか

62歳、今もコードを書いている。何年もインフラチームが AWS の資産管理を
スプレッドシートでやるのを見てきた——そして毎回、例外なく、そのスプレッドシートが
腐っていくのも見てきた。

ある日、当たり前の問いを立てた。tfstate も boto3 もあるのに、なぜまだ手作業で
やっているんだ？

もうひとつ気になっていたのが、ミドルウェアの EOL 管理だ。監視対象のリストはあった。
でもアラートもなく、ダッシュボードもなく、次のアクションにつながらない——
また別のスプレッドシートが静かに古くなっていくだけ。

だから、ずっと欲しかったツールを作った。定期スキャン・ドリフト検知・EOL アラート——
一箇所で、セルフホスト、データは自分のインフラの中に。

---

## 機能

| 機能 | 説明 |
|------|------|
| **資産台帳** | EC2 / ECS / Lambda / RDS / DynamoDB / ElastiCache / EFS / EKS / S3 / ALB / VPC / EBS / SNS / SQS / API Gateway / CloudFront / Route 53 ほかを一覧・検索 |
| **AWSスキャン** | AssumeRole で対象アカウントの 17 種以上のリソース（コンピュート・DB・ストレージ・ネットワーク・メッセージング）を自動検出 |
| **Terraform連携** | tfstate をアップロードして資産をインポート |
| **ドリフト検知** | tfstate と AWS 実態の属性レベルの差分を検出 |
| **ドリフト履歴** | 差分の推移を記録。スキャン/インポートのたびにスナップショットを保存し、推移グラフと各時点の差分を表示 |
| **ドリフトのリスク評価・犯人特定** | ドリフトをセキュリティ影響度で採点（例: セキュリティグループが `0.0.0.0/0` に開放）し、誰がそのリソースを変更したかを CloudTrail で特定 |
| **ドリフト・ブリーフィング** | システム単位の週次 Slack サマリ（任意）— 重大度の内訳、前週比のトレンド、危険な変更トップと実行者。`DRIFT_DIGEST_ENABLED` でオプトイン |
| **コマンドライン** *(オプションのプラグイン)* | `manage.py syncvey scan / drift / status` で、ターミナルや CI からスキャンとドリフト確認を実行 — ダッシュボードと同じエンジンを駆動。`drift --exit-code` はドリフトがあればビルドを失敗させ、`--format json` はパイプラインに渡せる。着脱可能で、アプリを外せばコマンドも消える |
| **アプリ管理** | 言語・フレームワーク・デプロイ方式・依存パッケージを環境別に記録 |
| **EOLアラート** | サポート終了のミドルウェア/ランタイムを警告（既定オフライン・任意で日次更新） |
| **構成図** | 環境内のリソース関係を可視化 |
| **マルチアカウント** | システムごとに IAM Role ARN を登録し複数 AWS アカウントを管理 |

---

## 技術スタック

| レイヤー | 技術 |
|----------|------|
| Backend | Python 3.12 / Django（サーバーサイドレンダリング・SPAなし） |
| Frontend | htmx 1.9 + Tailwind CSS — Django テンプレート。ビルド工程・Node 不要 |
| Database | PostgreSQL 18.3 |
| AWS SDK | boto3（AssumeRole によるクロスアカウントアクセス） |
| 認証 | TOTP による2段階認証（pyotp） |
| スケジューラ | django-apscheduler（定期スキャン） |
| インフラ | Docker Compose |

---

## セットアップ

### 前提条件

- Docker / Docker Compose

### 1. 環境変数の設定

```bash
cp .env.example .env
```

`.env` を編集：

```bash
# Django
SECRET_KEY=your-secret-key
DEBUG=True

# DB（docker-compose.yml と合わせる）
DATABASE_URL=postgres://user:password@db:5432/asset_manager

# AWS（中央アカウントの IAM ユーザーキー）
AWS_ACCOUNT_ID=123456789012
AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE
AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
AWS_SCAN_REGIONS=ap-northeast-1,ap-northeast-3
```

> **本番での注意:** ローカル以外にデプロイする場合は `DEBUG=False` にし、強固でユニークな `SECRET_KEY` を使ってください。

### 2. 起動

```bash
docker compose up -d
```

> **Tip:** VS Code を使うなら Dev Containers で開くと環境が自動構築されます。

### 3. マイグレーション＆（任意で）サンプルデータ投入

```bash
docker compose exec app python manage.py migrate
docker compose exec app python manage.py seed   # 任意: サンプルデータ投入
```

### 4. ブラウザでアクセス

| サービス | URL |
|----------|-----|
| アプリ | http://localhost:8000/ |
| Django Admin | http://localhost:8000/admin/ |

---

## AWSスキャンのセットアップ

各対象 AWS アカウントに ReadOnly の IAM Role を作成し、その ARN を SyncVey に登録します。

詳細手順 → [aws-setup.md](aws-setup.ja.md)

**概要：**
1. `.env` に中央アカウントの IAM ユーザーキーを設定
2. 付属の IAM ポリシー（[`iam/iam-policy.json`](iam/iam-policy.json)）で各対象アカウントに `SyncVeyReadOnly` ロールを作成
3. システムカードの 🛡 ボタンから Role ARN を登録
4. **ScanLine** ボタンで初回スキャンを実行

---

## 外部通信

SyncVey は完全セルフホスト型です。外部への送信通信は以下のみで、テレメトリや利用解析の送信はありません。

| 接続先 | タイミング | 方向 | 制御 |
|--------|-----------|------|------|
| AWS API（boto3 / AssumeRole） | 手動・定期スキャン時 | 送信 HTTPS | Role ARN を設定した場合のみ |
| AWS CloudTrail（`LookupEvents`） | 「Who changed this?」を押した時、または週次ブリーフィング | 送信 HTTPS | 遅延実行・自動では呼ばない。Role と `cloudtrail:LookupEvents` が必要 |
| `hooks.slack.com` | ドリフト検出時、または週次ブリーフィング | 送信 HTTPS | システム個別の Slack Webhook URL（オプトイン）。ブリーフィングは `DRIFT_DIGEST_ENABLED=true` も必要 |
| `endoflife.date` | EOL データの日次取得 | 送信 HTTPS | **既定OFF** — `EOL_REFRESH_ENABLED=true` で有効化 |

**EOL 取得について。** EOL 判定は内蔵データでオフライン動作します。
`EOL_REFRESH_ENABLED=true` にすると、[endoflife.date](https://endoflife.date/) から最新データを
日次で取得するジョブが有効になります（取得失敗時は内蔵データにフォールバック）。
取得対象は既定で「実際に登録している依存パッケージ」のみ。`EOL_REFRESH_DYNAMIC=false` で
既知の固定セットに限定できます。手動実行も可能です:

```bash
docker compose exec app python manage.py refresh_eol --force
```

---

## ルート

SyncVey は JSON REST API ではなく、htmx ベースのサーバーサイドレンダリングアプリです。
各ビューはレンダリング済みの HTML（ページ／パーシャル）を返します。

```
/                                      ダッシュボード
/systems/                              システム一覧
/systems/<id>/environments/            システム配下の環境
/systems/<id>/applications/            システム配下のアプリケーション
/environments/<id>/scan/               AWSスキャン実行
/environments/<id>/drift/              Drift レポート
/environments/<id>/drift/history/      Drift 履歴（推移）
/drift-risk/                           ドリフトのリスク評価・犯人特定
/drift-digest/                         ドリフト・ブリーフィング（週次 Slack サマリのプレビュー）
/environments/<id>/diagram/            構成図
/environments/<id>/sync-s3/            S3 からリモート tfstate を同期
/assets/                               資産一覧
/assets/<id>/                          資産詳細
/upload-tfstate/                       tfstate からインポート
/samples/                              サンプルライブラリ
/audit-log/                            監査ログ
/profile/                              プロフィール／2段階認証
/admin/                                Django Admin
```

全ルート → [asset_manager/urls.py](asset_manager/urls.py)

---

## コマンドライン *(オプションのプラグイン)*

`syncvey_cli` プラグインは `syncvey` 管理コマンドを追加し、オペレーターや CI
パイプラインが Web UI を開かずにスキャン実行とドリフト確認をできるようにする。
駆動するのはダッシュボードと同じスキャン/ドリフトのエンジンなので、「何をドリフト
とみなすか」で両者が食い違うことはない。

```bash
# ライブ AWS スキャンを実行しドリフトのスナップショットを記録（全システム or 1つ）
docker compose exec app python manage.py syncvey scan --system e-commerce

# 現在のドリフトを表示。--format json でパイプラインに渡せる
docker compose exec app python manage.py syncvey drift --env prod

# ドリフトがあればビルドを失敗させる — CI ステップに差し込む
docker compose exec app python manage.py syncvey drift --exit-code

# システム/環境を資産数と最終スキャン時刻つきで一覧
docker compose exec app python manage.py syncvey status
```

終了コードが CI の契約: `0` = 正常 / ドリフト無し、`1` = ドリフト検出
（`drift --exit-code` 時のみ）、`2` = スキャンジョブ失敗、またはセレクタが何にも
一致せず。他のプラグイン同様に着脱可能で、`INSTALLED_APPS` から `syncvey_cli`
を外せばコマンドも消える。

---

## データモデル

```
System（システム）
  └── Environment（環境: PROD / STG / DEV / QA）
        └── Asset（資産: asset_type + category。属性は JSON フィールドに格納）
  └── Application（アプリケーション）
        └── AppEnvConfig（環境別設定）
              └── AppDependency（依存パッケージ）
```

---

## 開発

```bash
# アプリコンテナのシェル
docker compose exec app bash

# モデル変更後のマイグレーション生成
docker compose exec app python manage.py makemigrations

# ログ確認
docker compose logs -f app
docker compose logs -f db
```

---

## ライセンス

[MIT](LICENSE)
