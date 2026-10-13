# SyncVey

**A self-hosted ledger of your AWS resources, with Terraform drift detection** — attribute-level drift between
your tfstate and live AWS, end-of-life dates for what you run (on AWS's own support schedule), and the publicly
known vulnerabilities of the libraries and database engines you use. Your data stays in your own infrastructure.

> **This image does not run by itself.** It needs PostgreSQL and a start command, both of which live in the
> `docker-compose.yml`. Use Docker Compose — it takes two files, and you don't have to clone anything.

## Start (no clone)

```bash
mkdir syncvey && cd syncvey
curl -O https://raw.githubusercontent.com/MR-TABATA/SyncVey/main/docker-compose.yml
curl -o .env https://raw.githubusercontent.com/MR-TABATA/SyncVey/main/.env.example
# edit .env: SECRET_KEY, and your AWS keys when you want to scan a real account
docker compose up -d
```

Then open <http://localhost:8000/>. Compose pulls this image and a PostgreSQL image; nothing is built.
Migrations and sample data run on first start. No AWS account yet? The README has a
[try it without an AWS account](https://github.com/MR-TABATA/SyncVey#try-it-without-an-aws-account) path.

## Or clone the repository

```bash
git clone https://github.com/MR-TABATA/SyncVey
cd SyncVey
cp .env.example .env
docker compose up -d
```

The repository also holds the IAM policy (`iam/iam-policy.json`) for the read-only role SyncVey scans with.

## Tags

- `latest` — the newest release
- `X.Y.Z` — a specific release (pin it with `SYNCVEY_IMAGE=jiniie/syncvey:X.Y.Z` in `.env`)

Platforms: `linux/amd64`, `linux/arm64`.

## Links

- Source and docs: <https://github.com/MR-TABATA/SyncVey>
- Product page: <https://mrktools.app/en/syncvey/>
- Release notes: <https://github.com/MR-TABATA/SyncVey/releases>
- License: MIT

---

### 日本語

SyncVey は、AWS のリソースを台帳にして、Terraform とのドリフトを検知する、セルフホスト型のツールです。
サポート終了（AWS 自身の日付）と、使っているライブラリ・DB エンジンの既知の脆弱性も、同じ画面で見られます。

**このイメージだけでは動きません。** PostgreSQL と起動コマンドが `docker-compose.yml` にあるためです。
Docker Compose で起動してください。clone は要らず、2つのファイルを取るだけです（上の「Start (no clone)」）。
手順の詳細は [GitHub の README](https://github.com/MR-TABATA/SyncVey) と [紹介ページ](https://mrktools.app/ja/syncvey/) にあります。
