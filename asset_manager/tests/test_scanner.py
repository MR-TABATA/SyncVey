"""
Boto3スキャナーのテスト。
Motoが本物のHTTPリクエストをインターセプトするため、
AWSクレデンシャルなしで実行できる。
"""

import boto3
from moto import mock_aws
from django.test import TestCase

from asset_manager.scanner import (
    scan_ec2, scan_rds, scan_ecs_services,
    scan_s3, scan_alb, scan_vpc, scan_ebs, scan_lambda,
)

REGION = 'ap-northeast-1'


def _session():
    return boto3.Session(region_name=REGION)


# ---------------------------------------------------------------------------
# EC2
# ---------------------------------------------------------------------------

@mock_aws
class TestScanEc2(TestCase):

    def test_empty_account_returns_empty_list(self):
        self.assertEqual(scan_ec2(_session()), [])

    def test_finds_running_instance(self):
        ec2 = boto3.client('ec2', region_name=REGION)
        ec2.run_instances(
            ImageId='ami-12345678',
            MinCount=1, MaxCount=1,
            InstanceType='t3.micro',
        )
        results = scan_ec2(_session())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['instance_type'], 't3.micro')
        self.assertEqual(results[0]['_resource_type'], 'aws_instance')
        self.assertEqual(results[0]['_scan_source'],   'boto3')

    def test_normalizes_tags_to_dict(self):
        ec2 = boto3.client('ec2', region_name=REGION)
        ec2.run_instances(
            ImageId='ami-12345678',
            MinCount=1, MaxCount=1,
            TagSpecifications=[{
                'ResourceType': 'instance',
                'Tags': [{'Key': 'Name', 'Value': 'web-server'}],
            }],
        )
        results = scan_ec2(_session())
        self.assertEqual(results[0]['tags'], {'Name': 'web-server'})

    def test_finds_multiple_instances(self):
        ec2 = boto3.client('ec2', region_name=REGION)
        ec2.run_instances(ImageId='ami-12345678', MinCount=3, MaxCount=3, InstanceType='t3.micro')
        results = scan_ec2(_session())
        self.assertEqual(len(results), 3)

    def test_instance_type_change_is_detectable(self):
        """tfstateにt3.microが記録されていて、実態がt3.smallに変わったケース"""
        ec2 = boto3.client('ec2', region_name=REGION)
        ec2.run_instances(ImageId='ami-12345678', MinCount=1, MaxCount=1, InstanceType='t3.small')
        results = scan_ec2(_session())
        # tfstateのinstance_typeが't3.micro'だとすると差分が出るはず
        self.assertEqual(results[0]['instance_type'], 't3.small')


# ---------------------------------------------------------------------------
# RDS
# ---------------------------------------------------------------------------

@mock_aws
class TestScanRds(TestCase):

    def test_empty_account_returns_empty_list(self):
        self.assertEqual(scan_rds(_session()), [])

    def test_finds_db_instance(self):
        rds = boto3.client('rds', region_name=REGION)
        rds.create_db_instance(
            DBInstanceIdentifier='mydb',
            DBInstanceClass='db.t3.micro',
            Engine='mysql',
            MasterUsername='admin',
            MasterUserPassword='password',
            AllocatedStorage=20,
        )
        results = scan_rds(_session())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['id'],             'mydb')
        self.assertEqual(results[0]['engine'],         'mysql')
        self.assertEqual(results[0]['instance_class'], 'db.t3.micro')
        self.assertEqual(results[0]['_resource_type'], 'aws_db_instance')

    def test_engine_version_change_is_detectable(self):
        """エンジンバージョンの変更（アップグレード等）を検知できること"""
        rds = boto3.client('rds', region_name=REGION)
        rds.create_db_instance(
            DBInstanceIdentifier='mydb',
            DBInstanceClass='db.t3.micro',
            Engine='mysql',
            EngineVersion='8.0.35',
            MasterUsername='admin',
            MasterUserPassword='password',
            AllocatedStorage=20,
        )
        results = scan_rds(_session())
        self.assertEqual(results[0]['engine_version'], '8.0.35')

    def test_multi_az_field_present(self):
        rds = boto3.client('rds', region_name=REGION)
        rds.create_db_instance(
            DBInstanceIdentifier='mydb',
            DBInstanceClass='db.t3.micro',
            Engine='mysql',
            MasterUsername='admin',
            MasterUserPassword='password',
            AllocatedStorage=20,
            MultiAZ=False,
        )
        results = scan_rds(_session())
        self.assertIn('multi_az', results[0])


# ---------------------------------------------------------------------------
# ECS
# ---------------------------------------------------------------------------

@mock_aws
class TestScanEcsServices(TestCase):

    def test_empty_account_returns_empty_list(self):
        self.assertEqual(scan_ecs_services(_session()), [])

    def test_finds_service(self):
        ecs = boto3.client('ecs', region_name=REGION)
        ecs.create_cluster(clusterName='my-cluster')
        ecs.register_task_definition(
            family='my-task',
            containerDefinitions=[{
                'name': 'app',
                'image': 'nginx:latest',
                'memory': 256,
            }],
        )
        ecs.create_service(
            cluster='my-cluster',
            serviceName='my-service',
            taskDefinition='my-task',
            desiredCount=2,
        )
        results = scan_ecs_services(_session())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['name'],          'my-service')
        self.assertEqual(results[0]['desired_count'], 2)
        self.assertEqual(results[0]['_resource_type'], 'aws_ecs_service')

    def test_desired_count_change_is_detectable(self):
        """ECSのdesired_countが変わっていることを検知できること"""
        ecs = boto3.client('ecs', region_name=REGION)
        ecs.create_cluster(clusterName='my-cluster')
        ecs.register_task_definition(
            family='my-task',
            containerDefinitions=[{'name': 'app', 'image': 'nginx', 'memory': 256}],
        )
        ecs.create_service(
            cluster='my-cluster',
            serviceName='my-service',
            taskDefinition='my-task',
            desiredCount=5,
        )
        results = scan_ecs_services(_session())
        # tfstateに desiredCount=2 が記録されていれば差分が出るはず
        self.assertEqual(results[0]['desired_count'], 5)


# ---------------------------------------------------------------------------
# S3
# ---------------------------------------------------------------------------

@mock_aws
class TestScanS3(TestCase):

    def test_empty_account_returns_empty_list(self):
        self.assertEqual(scan_s3(_session()), [])

    def test_finds_bucket(self):
        s3 = boto3.client('s3', region_name='us-east-1')
        s3.create_bucket(Bucket='my-bucket')
        results = scan_s3(_session())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['id'],             'my-bucket')
        self.assertEqual(results[0]['bucket'],         'my-bucket')
        self.assertEqual(results[0]['_resource_type'], 'aws_s3_bucket')
        self.assertEqual(results[0]['_scan_source'],   'boto3')

    def test_finds_multiple_buckets(self):
        s3 = boto3.client('s3', region_name='us-east-1')
        s3.create_bucket(Bucket='bucket-a')
        s3.create_bucket(Bucket='bucket-b')
        results = scan_s3(_session())
        self.assertEqual(len(results), 2)


# ---------------------------------------------------------------------------
# ALB
# ---------------------------------------------------------------------------

@mock_aws
class TestScanAlb(TestCase):

    def _create_alb(self, name='my-alb'):
        ec2 = boto3.client('ec2', region_name=REGION)
        vpc = ec2.create_vpc(CidrBlock='10.0.0.0/16')['Vpc']['VpcId']
        az1 = f'{REGION}a'
        az2 = f'{REGION}c'
        sn1 = ec2.create_subnet(VpcId=vpc, CidrBlock='10.0.1.0/24', AvailabilityZone=az1)['Subnet']['SubnetId']
        sn2 = ec2.create_subnet(VpcId=vpc, CidrBlock='10.0.2.0/24', AvailabilityZone=az2)['Subnet']['SubnetId']
        elb = boto3.client('elbv2', region_name=REGION)
        elb.create_load_balancer(Name=name, Subnets=[sn1, sn2])

    def test_empty_account_returns_empty_list(self):
        self.assertEqual(scan_alb(_session()), [])

    def test_finds_load_balancer(self):
        self._create_alb('my-alb')
        results = scan_alb(_session())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['name'],          'my-alb')
        self.assertEqual(results[0]['_resource_type'], 'aws_lb')
        self.assertEqual(results[0]['_scan_source'],   'boto3')

    def test_arn_and_dns_name_present(self):
        self._create_alb()
        results = scan_alb(_session())
        self.assertIn('arn',      results[0])
        self.assertIn('dns_name', results[0])


# ---------------------------------------------------------------------------
# VPC
# ---------------------------------------------------------------------------

@mock_aws
class TestScanVpc(TestCase):

    def test_finds_created_vpc(self):
        ec2 = boto3.client('ec2', region_name=REGION)
        ec2.create_vpc(CidrBlock='10.1.0.0/16')
        results = scan_vpc(_session())
        cidrs = [r['cidr_block'] for r in results]
        self.assertIn('10.1.0.0/16', cidrs)

    def test_resource_type_is_correct(self):
        ec2 = boto3.client('ec2', region_name=REGION)
        ec2.create_vpc(CidrBlock='10.2.0.0/16')
        results = scan_vpc(_session())
        self.assertTrue(all(r['_resource_type'] == 'aws_vpc' for r in results))

    def test_cidr_change_is_detectable(self):
        """異なるCIDRのVPCを識別できること"""
        ec2 = boto3.client('ec2', region_name=REGION)
        ec2.create_vpc(CidrBlock='192.168.0.0/24')
        results = scan_vpc(_session())
        cidrs = [r['cidr_block'] for r in results]
        self.assertIn('192.168.0.0/24', cidrs)


# ---------------------------------------------------------------------------
# EBS
# ---------------------------------------------------------------------------

@mock_aws
class TestScanEbs(TestCase):

    def test_empty_account_returns_empty_list(self):
        self.assertEqual(scan_ebs(_session()), [])

    def test_finds_volume(self):
        ec2 = boto3.client('ec2', region_name=REGION)
        ec2.create_volume(AvailabilityZone=f'{REGION}a', Size=20, VolumeType='gp3')
        results = scan_ebs(_session())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['size'],          20)
        self.assertEqual(results[0]['volume_type'],   'gp3')
        self.assertEqual(results[0]['_resource_type'], 'aws_ebs_volume')
        self.assertEqual(results[0]['_scan_source'],   'boto3')

    def test_volume_type_change_is_detectable(self):
        """gp2→gp3のような変更を検知できること"""
        ec2 = boto3.client('ec2', region_name=REGION)
        ec2.create_volume(AvailabilityZone=f'{REGION}a', Size=50, VolumeType='gp2')
        results = scan_ebs(_session())
        self.assertEqual(results[0]['volume_type'], 'gp2')


# ---------------------------------------------------------------------------
# Lambda
# ---------------------------------------------------------------------------

@mock_aws
class TestScanLambda(TestCase):

    def _create_role(self):
        iam = boto3.client('iam', region_name=REGION)
        return iam.create_role(
            RoleName='lambda-role',
            AssumeRolePolicyDocument='{"Version":"2012-10-17","Statement":[]}',
        )['Role']['Arn']

    def test_empty_account_returns_empty_list(self):
        self.assertEqual(scan_lambda(_session()), [])

    def test_finds_function(self):
        lm = boto3.client('lambda', region_name=REGION)
        lm.create_function(
            FunctionName='my-function',
            Runtime='python3.12',
            Role=self._create_role(),
            Handler='index.handler',
            Code={'ZipFile': b'def handler(e, c): pass'},
        )
        results = scan_lambda(_session())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['function_name'], 'my-function')
        self.assertEqual(results[0]['runtime'],       'python3.12')
        self.assertEqual(results[0]['_resource_type'], 'aws_lambda_function')
        self.assertEqual(results[0]['_scan_source'],   'boto3')

    def test_runtime_change_is_detectable(self):
        """ランタイムのバージョンアップを検知できること"""
        lm = boto3.client('lambda', region_name=REGION)
        lm.create_function(
            FunctionName='my-function',
            Runtime='python3.11',
            Role=self._create_role(),
            Handler='index.handler',
            Code={'ZipFile': b'def handler(e, c): pass'},
        )
        results = scan_lambda(_session())
        self.assertEqual(results[0]['runtime'], 'python3.11')
