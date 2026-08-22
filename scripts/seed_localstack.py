#!/usr/bin/env python3
"""Create a handful of fake AWS resources inside a local LocalStack.

This is the demo on-ramp: it gives `syncvey scan` something to find so the
ledger, the drift report and the dashboard are populated without an AWS
account, credentials or a bill.

    docker compose --profile demo up -d
    docker compose exec app python scripts/seed_localstack.py
    docker compose exec app python manage.py syncvey scan --system demo

Only the services LocalStack's free (community) edition implements are seeded.
RDS, ECS, ALB, ElastiCache, EFS, EKS, API Gateway v2 and CloudFront are
paid-tier there; their scanners error out and SyncVey reports that per service
rather than mistaking the failure for "everything was deleted".

Running it twice is fine — resources that already exist are left alone.
"""

import io
import os
import sys
import zipfile

import boto3
from botocore.exceptions import ClientError

REGION = os.getenv('AWS_SCAN_REGIONS', 'ap-northeast-1').split(',')[0].strip()

# 実アカウントに向けて走らせたら本物のリソースを作ってしまう。エミュレータを
# 指していることを確認できない限り、何もしないで止まる。
endpoint = os.getenv('AWS_ENDPOINT_URL', '')
if not endpoint:
    sys.exit("AWS_ENDPOINT_URL is not set — refusing to run against real AWS.\n"
             "Set AWS_ENDPOINT_URL=http://localstack:4566 (see .env.example).")
if not any(host in endpoint for host in ('localstack', 'localhost', '127.0.0.1')):
    sys.exit(f"AWS_ENDPOINT_URL={endpoint} does not look like a local emulator — "
             "refusing to run.")

session = boto3.Session(region_name=REGION)
created = []


def step(label, fn):
    """Run one creation step; report it, and never let one failure stop the rest."""
    try:
        fn()
        created.append((label, 'ok'))
    except ClientError as e:
        code = e.response.get('Error', {}).get('Code', '')
        if code in ('BucketAlreadyOwnedByYou', 'ResourceInUseException',
                    'ResourceConflictException', 'InvalidGroup.Duplicate',
                    'HostedZoneAlreadyExists', 'QueueAlreadyExists'):
            created.append((label, 'already there'))
        else:
            created.append((label, f"skipped — {code or type(e).__name__}"))
    except Exception as e:  # noqa: BLE001 - a demo seeder must not crash the demo
        created.append((label, f"skipped — {type(e).__name__}"))


def _lambda_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('index.py', "def handler(event, context):\n    return {'ok': True}\n")
    return buf.getvalue()


def network_and_compute():
    ec2 = session.client('ec2')
    vpc = ec2.create_vpc(CidrBlock='10.90.0.0/16')['Vpc']['VpcId']
    ec2.create_tags(Resources=[vpc], Tags=[{'Key': 'Name', 'Value': 'demo-vpc'}])
    subnet = ec2.create_subnet(VpcId=vpc, CidrBlock='10.90.1.0/24',
                               AvailabilityZone=f'{REGION}a')['Subnet']['SubnetId']
    for name in ('demo-web-01', 'demo-api-01'):
        ec2.run_instances(
            ImageId='ami-0abcdef1234567890', MinCount=1, MaxCount=1,
            InstanceType='t3.micro', SubnetId=subnet,
            TagSpecifications=[{'ResourceType': 'instance',
                                'Tags': [{'Key': 'Name', 'Value': name},
                                         {'Key': 'Env', 'Value': 'demo'}]}],
        )
    ec2.create_volume(
        AvailabilityZone=f'{REGION}a', Size=20,
        TagSpecifications=[{'ResourceType': 'volume',
                            'Tags': [{'Key': 'Name', 'Value': 'demo-data-vol'}]}],
    )


step('ec2 / vpc / ebs', network_and_compute)
step('s3', lambda: session.client('s3').create_bucket(
    Bucket='demo-app-assets',
    CreateBucketConfiguration={'LocationConstraint': REGION}))
step('sqs', lambda: session.client('sqs').create_queue(QueueName='demo-jobs'))
step('sns', lambda: session.client('sns').create_topic(Name='demo-alerts'))
step('dynamodb', lambda: session.client('dynamodb').create_table(
    TableName='demo-sessions',
    KeySchema=[{'AttributeName': 'id', 'KeyType': 'HASH'}],
    AttributeDefinitions=[{'AttributeName': 'id', 'AttributeType': 'S'}],
    BillingMode='PAY_PER_REQUEST'))
step('secretsmanager', lambda: session.client('secretsmanager').create_secret(
    Name='demo/db/password', SecretString='not-a-real-secret'))
step('lambda', lambda: session.client('lambda').create_function(
    FunctionName='demo-image-resize', Runtime='python3.12',
    Role='arn:aws:iam::000000000000:role/demo', Handler='index.handler',
    Code={'ZipFile': _lambda_zip()}))
step('route53', lambda: session.client('route53').create_hosted_zone(
    Name='demo.internal.', CallerReference='syncvey-demo-seed'))

width = max(len(label) for label, _ in created)
print(f"LocalStack seed — endpoint {endpoint}, region {REGION}\n")
for label, outcome in created:
    print(f"  {label.ljust(width)}  {outcome}")
print("\nNext: docker compose exec app python manage.py syncvey scan")
