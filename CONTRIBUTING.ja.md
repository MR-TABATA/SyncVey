# 開発ガイドライン

[English](CONTRIBUTING.md) | **日本語**

SyncVey の改善に興味を持っていただきありがとうございます。

## 開発環境

**Dev Containers（推奨）。** VS Code でリポジトリを開き **Reopen in Container**
を選択してください。必要な拡張機能と仮想環境（`.venv`）が自動でセットアップされます。

**または Docker Compose。** `docker compose up --build -d`（詳細は
[Docker セットアップ手順](docker-setup.ja.md)）。

## 技術スタック

サーバーサイドレンダリングの Django アプリです。**SPA も REST フレームワークも
ありません。**

- **バックエンド:** Python 3.12 / Django。ビューは JSON ではなく
  **HTML**（フルページ or htmx パーシャル）を返します。
- **フロントエンド:** Django テンプレート内の htmx + Tailwind CSS。
  **ビルドステップなし・Node ツールチェーンなし**（Tailwind は Play CDN 読み込み）。
- **データベース:** PostgreSQL。

## コーディング規約

- **Python:** 変数・関数は `snake_case`、クラスは `PascalCase`。
  標準的な Django の流儀に従い、ビューは薄く保ちます。
- **テンプレート:** Tailwind のユーティリティクラスを使い、既存のパーシャル
  （`templates/_*.html`）と htmx パターン（`#main-content` を狙う
  `hx-get` / `hx-post`）を再利用してください。
- 周囲のコードの作法（命名・コメント量・イディオム）に合わせます。

## テスト

```bash
docker compose exec app pip install -r requirements-dev.txt
docker compose exec app pytest
```

`moto` が AWS をモックし、`pytest-django` と `pytest-playwright` が
ユニット・E2E テストをカバーします。

## Git 運用

- **ブランチ:** `feature/` または `fix/` プレフィックスを使用してください。
- **プルリクエスト:**
  - タイトル例: `fix: 資産一覧のソート不具合を修正 (#123)`
  - 影響範囲とテスト方法を明記してください。
