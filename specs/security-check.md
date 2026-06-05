# SyncVey — セキュリティ静的チェック 仕様・結果

実施日: 2026-06-04
対象: リリース前の Django バックエンド（`asset_manager` / `config`）
結果: **要対応の指摘はすべて是正済み**（bandit Medium/High = 0 / `check --deploy` 本番シミュ 6→2件=環境依存の opt-in のみ）

> 目的: 著者（実装者）の自己レビューは盲点が相関するため、まず**独立・決定論的なツール**で客観シグナルを取る。
> 本書はその実施仕様・結果・是正の証跡。機能面の検証は [test-spec.md](test-spec.md) を参照。

---

## 使用ツール

| ツール | 目的 | 実行コマンド |
|---|---|---|
| `manage.py check --deploy` | 本番設定の問題検出（DEBUG/SECRET_KEY/SSL/Cookie 等） | `python manage.py check --deploy` |
| bandit | Python 静的セキュリティ解析（テスト/マイグレーションは対象外） | `bandit -r asset_manager config -x '*/tests/*,*/migrations/*'` |
| pip-audit | 依存ライブラリの既知 CVE | `pip-audit` |
| OWASP ZAP (Baseline) | 動的スキャン（DAST）: ヘッダ/Cookie/XSS/CSRF 等を稼働中アプリで検査 | `zap-baseline.py -t http://host.docker.internal:8000 -I` |

※ bandit / pip-audit は dev 依存。コンテナ未導入時は `pip install bandit pip-audit` で一時導入可。

---

## 指摘と是正

| 重大度 | 指摘 | 箇所 | 是正 | コミット |
|---|---|---|---|---|
| 🔴 High相当 | **SSRF / file:** — ユーザーが system 毎に設定する Slack Webhook URL を未検証で `urlopen`（bandit B310 Medium）。`http://169.254.169.254/...`（メタデータ窃取）や `file:///etc/passwd` に悪用され得る | `asset_manager/notifications.py:86` | `_is_allowed_webhook` で `https` かつ host=`hooks.slack.com` に厳格制限（`urlparse().hostname`）。送信時＋保存時（`update_system_view`）の両方で拒否し、編集フォームにエラー表示。拒否テスト追加。bandit Medium 1→0 | `1b0511a` |
| 🟡 Medium | **本番設定の危険なデフォルト** — `DEBUG=True` ハードコード / `SECRET_KEY` に安全でない既定 / `ALLOWED_HOSTS=["*"]` / Cookie 非Secure | `config/settings.py` | DEBUG を env 化（既定 False）／DEBUG=False かつ既定鍵なら起動拒否（`ImproperlyConfigured`）／ALLOWED_HOSTS を env 明示（DEBUG時のみ `*`）／Cookie 本番既定 Secure／`CSRF_TRUSTED_ORIGINS`・`SECURE_SSL_REDIRECT`・HSTS・`PROXY_SSL_HEADER` を env トグル化。`.env.example` 文書化 | `22e86ae` |
| 🟡 Low | **pip の既知 CVE**（install 時の tar/zip 展開系: CVE-2026-1703/3219/6357 ほか） | Docker イメージの pip | `Dockerfile` でビルド時に `pip install --upgrade pip`。CVE-2025-8869 は Python 3.12 の PEP 706 実装で既に緩和済み | `70f81f2` |
| ⚪ 誤検出 | `diagrams 0.25.1` PYSEC-2024-270 | requirements | **誤検出**。CVE 本文は別パッケージ「Airflow-Diagrams」の `cli.py unsafe_load` の話で、本プロジェクトが使う mingrammer の `diagrams`（AWS 構成図）とは無関係 → 対応不要 | — |

### SSRF 対策の検証観点
`_is_allowed_webhook` は `urlparse().hostname` でホストを判定するため、以下の迂回も拒否する:
- `https://hooks.slack.com@evil.com/` → hostname=`evil.com` → 拒否
- `https://hooks.slack.com.evil.com/` → hostname 不一致 → 拒否
- `http://hooks.slack.com/...`（非 https）→ 拒否
- `file://` / `gopher://` 等 → scheme 不一致で拒否

ホスト名は Slack 固定（攻撃者が DNS を制御できない）ため allowlist は堅牢。

---

## 動的スキャン（DAST: OWASP ZAP Baseline）

実施日: 2026-06-04 / 対象: 稼働中アプリ（dev: DEBUG=True / runserver）
結果: **FAIL: 0 / WARN: 14 / PASS: 53**（致命的・高リスクなし）

> SAST が拾えない実行時の問題（セキュリティヘッダ・Cookie・XSS・CSRF）を、独立した DAST ツールで確認。

### WARN の仕分けと是正

| 区分 | 警告 | 対処 |
|---|---|---|
| 実対応 | CSP 未設定 / Permissions-Policy 未設定 | `SecurityHeadersMiddleware` で CSP・Permissions-Policy を付与、`SECURE_REFERRER_POLICY` を設定 |
| 実対応 | CDN JS の SRI 欠如 / `lucide@latest` 未固定 | htmx・lucide をバージョン固定し `integrity`(sha384)+`crossorigin="anonymous"` を付与（Tailwind は据え置き＝後述の受容リスク） |
| dev 由来 | debug_toolbar.js のコメント / Server バナーのバージョン / 静的の nosniff 欠如 | 本番（DEBUG=False＋gunicorn＋whitenoise/nginx）で解消。dev runserver 固有 |
| 情報ノイズ | Non-Storable / Modern Web App / Auth・Session Identified / COEP | 脆弱性ではない（ZAP の情報ルール） |

実装後の確認: CSP/Permissions-Policy/Referrer-Policy/X-Frame/nosniff の付与をヘッダで確認。ブラウザで **CSP違反・SRI失敗 0**、アプリ（ログイン/ダッシュボード/モーダル/htmx/lucideアイコン）正常動作を確認。

### CSP の方針
本アプリは Tailwind(Play CDN)・htmx・lucide を CDN 利用し、テンプレートに多数のインライン script/style を持つため、`script-src`/`style-src` に `'unsafe-inline'`、Tailwind の in-browser コンパイルのため `'unsafe-eval'` を許容する。一方で `frame-ancestors 'none'` / `base-uri 'self'` / `form-action 'self'` / `connect-src 'self'` は厳格に締めており、**外部スクリプト読込・iframe 埋め込み（クリックジャッキング）・フォーム送信先乗っ取り・外部への送信を遮断**する。

---

## 受容したリスク（判断して残す）

- **`'unsafe-eval'`（CSP）— Tailwind Play CDN 由来**
  - 残リスク: eval ベースの XSS 悪用。ただし「既に XSS が存在する」前提でのみ成立し、`'unsafe-inline'` が残る限り単独除去の ROI は低い。
  - 発動条件: ① フロントにビルド工程を導入する時（Tailwind プリコンパイル → `'unsafe-eval'` を除去）／② 監査・コンプラで eval 禁止が要求された時。
  - それまでは **受容**（Node ビルド導入の保守負担・依存増を避けるため意図的に先送り）。
- **`'unsafe-inline'`（CSP）— 多数のインライン script/style 由来**
  - 完全除去には全インライン script の nonce 化（大きめのリファクタ）が必要。上記ビルド導入時にまとめて検討する。

---

## 残課題（リリースブロッカーではない）

- **bandit Low 46件 → 是正済み**: 本番コード（asset_manager/config、テスト除く）は **Low/Medium/High すべて 0 件**。
  - 内訳: 29件は `assert`（pytest テストコード→対象外）、13件はテストのダミーパスワード（→対象外）。
  - 非テスト 5件は是正: `try/except/pass` 2件（views=ログ出力化 / signals=best-effort明示）、誤検出3件（SECRET_KEY既定値・マスク `'***'`・2FAクリア `''`）は `# nosec B105/B110` で理由付き抑制。
- **`check --deploy` 残り2件**（W004 HSTS / W008 SSL_REDIRECT）: HTTPS 環境依存のため意図的に env opt-in（既定 OFF）。HTTPS 運用時に `SECURE_HSTS_SECONDS` / `SECURE_SSL_REDIRECT` を有効化する。
- **独立 AI レビュー（レイヤー②）**: 静的ツールでは拾えない認可漏れ（IDOR）・ビジネスロジックのバグは、別文脈のレビューで別途確認する。

---

## 再現手順

```bash
# コンテナ内（dev 依存を一時導入する場合）
docker exec syncvey-app pip install -q bandit pip-audit

# ① 本番設定チェック（DEBUG=False 想定の値で）
docker exec -e DEBUG=False -e SECRET_KEY=<secret> -e ALLOWED_HOSTS=<host> \
  syncvey-app python manage.py check --deploy

# ② 静的セキュリティ解析（テスト/マイグレーション除外。本番コードは 0 件であること）
docker exec syncvey-app sh -c "cd /app && bandit -r asset_manager config -x '*/tests/*,*/migrations/*'"

# ③ 依存の CVE
docker exec syncvey-app sh -c "cd /app && pip-audit --desc"

# ④ 動的スキャン（DAST）— 稼働中アプリへ（Mac: host.docker.internal）
docker run --rm -t ghcr.io/zaproxy/zaproxy:stable \
  zap-baseline.py -t http://host.docker.internal:8000 -I
```

判定基準: **bandit Medium/High = 0**、`check --deploy` は環境依存の opt-in 警告のみ、pip-audit は誤検出/install時のみであること。
