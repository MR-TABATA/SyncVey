# AWS スキャンのセットアップ

[English](aws-setup.md) | **日本語**

SyncVey は、中央アカウントから各対象アカウントの IAM ロールを引き受けて、AWS
リソースを **読み取り専用** で検出します。リソースの作成・変更・削除は一切行いません。

## 仕組み

- **中央アカウント**の IAM ユーザー認証情報を `.env`
  （`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`）に設定します。
- **各対象アカウント**に、中央アカウントが引き受け可能な読み取り専用 IAM ロールを作成します。
- そのロールの **ARN** を SyncVey に登録します。スキャンは `AssumeRole` を呼び出し、
  boto3 でリソースのメタデータを読み取ります。

## 1. 中央アカウントの認証情報

`.env` に設定します：

```bash
AWS_ACCOUNT_ID=111122223333
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
AWS_SCAN_REGIONS=ap-northeast-1,us-east-1
```

## 2. 各対象アカウントに読み取り専用ロールを作成

IAM ロール（例: `SyncVeyReadOnly`）を以下の2つのポリシーで作成します。

**アクセス許可ポリシー** — [`iam/iam-policy.json`](iam/iam-policy.json) を使用します。
コンピュート（EC2 / ECS / EKS / Lambda）、データベース（RDS / ElastiCache /
DynamoDB / Redshift）、ストレージ（S3）、ネットワーク（ELB / CloudFront /
Route 53 / API Gateway）、セキュリティ（IAM / KMS / WAF / GuardDuty /
Secrets Manager）、可観測性（CloudWatch）への読み取り専用アクセスを付与します。

**信頼ポリシー** — 中央アカウントにロールの引き受けを許可します：

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "AWS": "arn:aws:iam::<中央アカウントID>:root" },
    "Action": "sts:AssumeRole"
  }]
}
```

### AWS CLI を使う場合

```bash
# trust-policy.json = 上記の JSON（中央アカウントID を埋める）
aws iam create-role \
  --role-name SyncVeyReadOnly \
  --assume-role-policy-document file://trust-policy.json

aws iam put-role-policy \
  --role-name SyncVeyReadOnly \
  --policy-name SyncVeyReadOnly \
  --policy-document file://iam/iam-policy.json
```

対象アカウントごとに繰り返します（または自前の IaC / StackSets に組み込みます）。

> **単一アカウント構成の場合**: 同じアカウント内にロールを作成し、信頼ポリシーを
> そのアカウント宛にします。中央 IAM ユーザーがそのロールを引き受けてスキャンします。

## 3. SyncVey にロール ARN を登録

システムカードの 🛡 ボタンをクリックし、ロール ARN を貼り付けます：

```
arn:aws:iam::<対象アカウントID>:role/SyncVeyReadOnly
```

## 4. 初回スキャンの実行

環境の **ScanLine** ボタンで初回スキャンを実行します。検出されたリソースは資産台帳に
表示され、tfstate との差分はドリフトレポートに表示されます。
