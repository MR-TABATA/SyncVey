"""
scanner.py
----------
Boto3-based AWS resource scanner.

Normalizes AWS API responses into Terraform-compatible attribute dicts
so they can be compared against tfstate-imported raw_data using the
existing _compute_raw_diff() logic in views.py.
"""

import boto3
from botocore.exceptions import ClientError
from django.utils import timezone

from .models import Asset
from .resource_registry import resolve_resource_type, resolve_provider


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

def get_session(role_arn=None, region='ap-northeast-1'):
    """Return a boto3 Session, optionally via STS AssumeRole."""
    if role_arn:
        sts = boto3.client('sts')
        resp = sts.assume_role(RoleArn=role_arn, RoleSessionName='syncvey-scan')
        creds = resp['Credentials']
        return boto3.Session(
            aws_access_key_id=creds['AccessKeyId'],
            aws_secret_access_key=creds['SecretAccessKey'],
            aws_session_token=creds['SessionToken'],
            region_name=region,
        )
    return boto3.Session(region_name=region)


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def _tags(raw):
    """[{'Key': k, 'Value': v}]  →  {k: v}"""
    if not raw:
        return {}
    return {t['Key']: t['Value'] for t in raw}


# ---------------------------------------------------------------------------
# Per-resource scanners  →  list[dict]  (Terraform-compatible attribute dicts)
# ---------------------------------------------------------------------------

def scan_ec2(session):
    ec2 = session.client('ec2')
    results = []
    paginator = ec2.get_paginator('describe_instances')
    for page in paginator.paginate():
        for reservation in page['Reservations']:
            for i in reservation['Instances']:
                results.append({
                    'id':                i['InstanceId'],
                    'ami':               i.get('ImageId', ''),
                    'instance_type':     i.get('InstanceType', ''),
                    'availability_zone': i.get('Placement', {}).get('AvailabilityZone', ''),
                    'subnet_id':         i.get('SubnetId', ''),
                    'vpc_id':            i.get('VpcId', ''),
                    'private_ip':        i.get('PrivateIpAddress', ''),
                    'public_ip':         i.get('PublicIpAddress', ''),
                    'key_name':          i.get('KeyName', ''),
                    'instance_state':    i.get('State', {}).get('Name', ''),
                    'tags':              _tags(i.get('Tags')),
                    '_resource_type':    'aws_instance',
                    '_scan_source':      'boto3',
                })
    return results


def scan_rds(session):
    rds = session.client('rds')
    results = []
    paginator = rds.get_paginator('describe_db_instances')
    for page in paginator.paginate():
        for db in page['DBInstances']:
            results.append({
                'id':                   db['DBInstanceIdentifier'],
                'engine':               db.get('Engine', ''),
                'engine_version':       db.get('EngineVersion', ''),
                'instance_class':       db.get('DBInstanceClass', ''),
                'allocated_storage':    db.get('AllocatedStorage', 0),
                'multi_az':             db.get('MultiAZ', False),
                'publicly_accessible':  db.get('PubliclyAccessible', False),
                'db_subnet_group_name': db.get('DBSubnetGroup', {}).get('DBSubnetGroupName', ''),
                'availability_zone':    db.get('AvailabilityZone', ''),
                'tags':                 _tags(db.get('TagList')),
                '_resource_type':       'aws_db_instance',
                '_scan_source':         'boto3',
            })
    return results


def scan_ecs_services(session):
    ecs = session.client('ecs')
    results = []
    cluster_arns = ecs.list_clusters().get('clusterArns', [])
    for cluster_arn in cluster_arns:
        paginator = ecs.get_paginator('list_services')
        for page in paginator.paginate(cluster=cluster_arn):
            service_arns = page.get('serviceArns', [])
            if not service_arns:
                continue
            services = ecs.describe_services(
                cluster=cluster_arn,
                services=service_arns,
            ).get('services', [])
            for svc in services:
                results.append({
                    'id':              svc['serviceArn'],
                    'name':            svc['serviceName'],
                    'cluster':         svc['clusterArn'],
                    'task_definition': svc.get('taskDefinition', ''),
                    'desired_count':   svc.get('desiredCount', 0),
                    'launch_type':     svc.get('launchType', ''),
                    'status':          svc.get('status', ''),
                    'tags':            _tags(svc.get('tags')),
                    '_resource_type':  'aws_ecs_service',
                    '_scan_source':    'boto3',
                })
    return results


def scan_s3(session):
    s3 = session.client('s3', region_name='us-east-1')
    results = []
    buckets = s3.list_buckets().get('Buckets', [])
    for b in buckets:
        name = b['Name']
        try:
            region = s3.get_bucket_location(Bucket=name).get('LocationConstraint') or 'us-east-1'
        except ClientError:
            region = ''
        try:
            tags = _tags(s3.get_bucket_tagging(Bucket=name).get('TagSet', []))
        except ClientError:
            tags = {}
        results.append({
            'id':             name,
            'bucket':         name,
            'region':         region,
            'tags':           tags,
            '_resource_type': 'aws_s3_bucket',
            '_scan_source':   'boto3',
        })
    return results


def scan_alb(session):
    elb = session.client('elbv2')
    results = []
    paginator = elb.get_paginator('describe_load_balancers')
    for page in paginator.paginate():
        for lb in page['LoadBalancers']:
            results.append({
                'id':                  lb['LoadBalancerArn'],
                'arn':                 lb['LoadBalancerArn'],
                'name':                lb['LoadBalancerName'],
                'dns_name':            lb.get('DNSName', ''),
                'scheme':              lb.get('Scheme', ''),
                'load_balancer_type':  lb.get('Type', ''),
                'vpc_id':              lb.get('VpcId', ''),
                '_resource_type':      'aws_lb',
                '_scan_source':        'boto3',
            })
    return results


def scan_vpc(session):
    ec2 = session.client('ec2')
    results = []
    paginator = ec2.get_paginator('describe_vpcs')
    for page in paginator.paginate():
        for vpc in page['Vpcs']:
            results.append({
                'id':              vpc['VpcId'],
                'cidr_block':      vpc.get('CidrBlock', ''),
                'is_default':      vpc.get('IsDefault', False),
                'state':           vpc.get('State', ''),
                'tags':            _tags(vpc.get('Tags')),
                '_resource_type':  'aws_vpc',
                '_scan_source':    'boto3',
            })
    return results


def scan_ebs(session):
    ec2 = session.client('ec2')
    results = []
    paginator = ec2.get_paginator('describe_volumes')
    for page in paginator.paginate():
        for vol in page['Volumes']:
            results.append({
                'id':                  vol['VolumeId'],
                'size':                vol.get('Size', 0),
                'volume_type':         vol.get('VolumeType', ''),
                'availability_zone':   vol.get('AvailabilityZone', ''),
                'state':               vol.get('State', ''),
                'encrypted':           vol.get('Encrypted', False),
                'iops':                vol.get('Iops', 0),
                'tags':                _tags(vol.get('Tags')),
                '_resource_type':      'aws_ebs_volume',
                '_scan_source':        'boto3',
            })
    return results


def scan_lambda(session):
    fn = session.client('lambda')
    results = []
    paginator = fn.get_paginator('list_functions')
    for page in paginator.paginate():
        for f in page['Functions']:
            results.append({
                'id':               f['FunctionArn'],
                'arn':              f['FunctionArn'],
                'function_name':    f['FunctionName'],
                'runtime':          f.get('Runtime', ''),
                'handler':          f.get('Handler', ''),
                'memory_size':      f.get('MemorySize', 0),
                'timeout':          f.get('Timeout', 0),
                'role':             f.get('Role', ''),
                '_resource_type':   'aws_lambda_function',
                '_scan_source':     'boto3',
            })
    return results


# ---------------------------------------------------------------------------
# Scanner registry  (resource_type → scanner function)
# ---------------------------------------------------------------------------

SCANNERS = [
    ('aws_instance',        scan_ec2),
    ('aws_db_instance',     scan_rds),
    ('aws_ecs_service',     scan_ecs_services),
    ('aws_s3_bucket',       scan_s3),
    ('aws_lb',              scan_alb),
    ('aws_vpc',             scan_vpc),
    ('aws_ebs_volume',      scan_ebs),
    ('aws_lambda_function', scan_lambda),
]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_scan(system, environment):
    """
    Scan all configured AWS regions for a system and upsert Assets.

    Returns:
        {'scanned': int, 'created': int, 'updated': int, 'errors': list[str]}
    """
    regions = system.aws_scan_regions or ['ap-northeast-1']
    result = {'scanned': 0, 'created': 0, 'updated': 0, 'errors': []}

    for region in regions:
        try:
            session = get_session(system.aws_role_arn, region)
        except Exception as e:
            result['errors'].append(f"{region}: session error — {e}")
            continue

        for resource_type, scanner_fn in SCANNERS:
            try:
                items = scanner_fn(session)
            except ClientError as e:
                result['errors'].append(f"{region}/{resource_type}: {e}")
                continue

            for attrs in items:
                result['scanned'] += 1
                cloud_id = attrs.get('id') or attrs.get('arn', '')
                if not cloud_id:
                    continue

                asset_type, asset_category = resolve_resource_type(resource_type, attrs)
                provider = resolve_provider(resource_type)
                name = attrs.get('name') or cloud_id

                asset, created = Asset.objects.get_or_create(
                    cloud_id=cloud_id,
                    defaults={
                        'environment':      environment,
                        'name':             name,
                        'provider':         provider,
                        'asset_type':       asset_type,
                        'asset_category':   asset_category,
                        'region':           region,
                        'raw_data':         attrs,
                        'last_imported_at': timezone.now(),
                    },
                )
                if created:
                    result['created'] += 1
                else:
                    asset.raw_data_prev    = asset.raw_data
                    asset.raw_data         = attrs
                    asset.last_imported_at = timezone.now()
                    asset.save(update_fields=['raw_data', 'raw_data_prev', 'last_imported_at'])
                    result['updated'] += 1

    return result
