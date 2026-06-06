# AWS Scan Setup

**English** | [日本語](aws-setup.ja.md)

SyncVey discovers resources in your AWS accounts **read-only**, by assuming an
IAM role in each target account from your central account. It never creates,
modifies, or deletes anything.

## How it works

- Your **central account** holds an IAM user whose credentials go in `.env`
  (`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`).
- In **each target account**, you create a read-only IAM role that the central
  account is allowed to assume.
- You register that role's **ARN** in SyncVey. Scans call `AssumeRole` and read
  resource metadata via boto3.

## 1. Central account credentials

Set these in `.env`:

```bash
AWS_ACCOUNT_ID=111122223333
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
AWS_SCAN_REGIONS=ap-northeast-1,us-east-1
```

## 2. Create the read-only role in each target account

Create an IAM role (e.g. `SyncVeyReadOnly`) with the two policies below.

**Permissions policy** — use [`iam/iam-policy.json`](iam/iam-policy.json). It grants
read-only access across compute (EC2 / ECS / EKS / Lambda), databases
(RDS / ElastiCache / DynamoDB / Redshift), storage (S3), networking
(ELB / CloudFront / Route 53 / API Gateway), security (IAM / KMS / WAF /
GuardDuty / Secrets Manager), and observability (CloudWatch).

**Trust policy** — allow your central account to assume the role:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "AWS": "arn:aws:iam::<CENTRAL_ACCOUNT_ID>:root" },
    "Action": "sts:AssumeRole"
  }]
}
```

### Using the AWS CLI

```bash
# trust-policy.json = the JSON block above, with your central account ID
aws iam create-role \
  --role-name SyncVeyReadOnly \
  --assume-role-policy-document file://trust-policy.json

aws iam put-role-policy \
  --role-name SyncVeyReadOnly \
  --policy-name SyncVeyReadOnly \
  --policy-document file://iam/iam-policy.json
```

Repeat for each target account (or fold it into your own IaC / StackSets).

> **Single-account setup?** Create the role in the same account and set its trust
> policy to that account. The central IAM user still assumes the role to scan.

## 3. Register the role ARN in SyncVey

On the system card, click the 🛡 button and paste the role ARN:

```
arn:aws:iam::<TARGET_ACCOUNT_ID>:role/SyncVeyReadOnly
```

## 4. Run the first scan

Click **ScanLine** on the environment to run the first scan. Discovered
resources appear in the asset ledger, and any drift against your tfstate is
flagged in the drift report.
