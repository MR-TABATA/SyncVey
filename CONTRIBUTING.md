# 開発ガイドライン (CONTRIBUTING.md)

## 🏗 開発環境の起動
本プロジェクトは **Dev Containers** を推奨しています。
1. VS Codeでリポジトリを開き、`Reopen in Container` を選択してください。
2. 必要な拡張機能と仮想環境（.venv）が自動でセットアップされます。

## 📏 コーディング規約
プラットフォーム間の整合性を保つため、以下の規則を適用します。

### 命名規則
- **Frontend (React)**: 
  - 変数・関数名: `camelCase` (例: `assetList`)
  - コンポーネント名: `PascalCase` (例: `AssetTable`)
- **Backend (Django/DRF)**:
  - 変数・関数名: `snake_case` (例: `get_asset_detail`)
  - クラス名: `PascalCase`
  - **APIレスポンス**: フロントエンドとの親和性のため `camelCase` で返却することを推奨します。

## 🌿 Git運用
- **ブランチ**: `feature/` または `fix/` プレフィックスを使用してください。
- **プルリクエスト (PR)**:
  - タイトル: `[fix] 資産一覧のソート不具合を修正 (#123)`
  - テンプレートに従い、影響範囲とテスト項目を明記してください。