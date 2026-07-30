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
                tags = _tags(i.get('Tags'))
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
                    # EC2 stamps ASG-launched instances with this reserved tag —
                    # surfaced explicitly so drift can tell churn from real adds
                    # without a DescribeAutoScalingGroups call or extra IAM.
                    'autoscaling_group': tags.get('aws:autoscaling:groupName', ''),
                    'tags':              tags,
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


def scan_dynamodb(session):
    ddb = session.client('dynamodb')
    results = []
    paginator = ddb.get_paginator('list_tables')
    for page in paginator.paginate():
        for name in page.get('TableNames', []):
            try:
                t = ddb.describe_table(TableName=name)['Table']
            except ClientError:
                continue
            results.append({
                'id':              name,
                'name':            name,
                'arn':             t.get('TableArn', ''),
                'billing_mode':    (t.get('BillingModeSummary') or {}).get('BillingMode', 'PROVISIONED'),
                'read_capacity':   (t.get('ProvisionedThroughput') or {}).get('ReadCapacityUnits', 0),
                'write_capacity':  (t.get('ProvisionedThroughput') or {}).get('WriteCapacityUnits', 0),
                'table_status':    t.get('TableStatus', ''),
                'item_count':      t.get('ItemCount', 0),
                '_resource_type':  'aws_dynamodb_table',
                '_scan_source':    'boto3',
            })
    return results


def scan_elasticache(session):
    ec = session.client('elasticache')
    results = []
    paginator = ec.get_paginator('describe_cache_clusters')
    for page in paginator.paginate():
        for c in page.get('CacheClusters', []):
            results.append({
                'id':                   c['CacheClusterId'],
                'name':                 c['CacheClusterId'],
                'engine':               c.get('Engine', ''),
                'engine_version':       c.get('EngineVersion', ''),
                'node_type':            c.get('CacheNodeType', ''),
                'num_cache_nodes':      c.get('NumCacheNodes', 0),
                'cache_cluster_status': c.get('CacheClusterStatus', ''),
                '_resource_type':       'aws_elasticache_cluster',
                '_scan_source':         'boto3',
            })
    return results


def scan_efs(session):
    efs = session.client('efs')
    results = []
    paginator = efs.get_paginator('describe_file_systems')
    for page in paginator.paginate():
        for fs in page.get('FileSystems', []):
            results.append({
                'id':                      fs['FileSystemId'],
                'name':                    fs.get('Name') or fs['FileSystemId'],
                'performance_mode':        fs.get('PerformanceMode', ''),
                'throughput_mode':         fs.get('ThroughputMode', ''),
                'encrypted':               fs.get('Encrypted', False),
                'lifecycle_state':         fs.get('LifeCycleState', ''),
                'number_of_mount_targets': fs.get('NumberOfMountTargets', 0),
                'tags':                    _tags(fs.get('Tags')),
                '_resource_type':          'aws_efs_file_system',
                '_scan_source':            'boto3',
            })
    return results


def scan_eks(session):
    eks = session.client('eks')
    results = []
    paginator = eks.get_paginator('list_clusters')
    for page in paginator.paginate():
        for name in page.get('clusters', []):
            try:
                c = eks.describe_cluster(name=name)['cluster']
            except ClientError:
                continue
            results.append({
                'id':              name,
                'name':            name,
                'arn':             c.get('arn', ''),
                'version':         c.get('version', ''),
                'status':          c.get('status', ''),
                'role_arn':        c.get('roleArn', ''),
                'endpoint':        c.get('endpoint', ''),
                'tags':            c.get('tags') or {},
                '_resource_type':  'aws_eks_cluster',
                '_scan_source':    'boto3',
            })
    return results


def scan_sns(session):
    sns = session.client('sns')
    results = []
    paginator = sns.get_paginator('list_topics')
    for page in paginator.paginate():
        for t in page.get('Topics', []):
            arn = t['TopicArn']
            results.append({
                'id':              arn,
                'arn':             arn,
                'name':            arn.split(':')[-1],
                '_resource_type':  'aws_sns_topic',
                '_scan_source':    'boto3',
            })
    return results


def scan_sqs(session):
    sqs = session.client('sqs')
    results = []
    for url in sqs.list_queues().get('QueueUrls', []):
        try:
            attrs = sqs.get_queue_attributes(
                QueueUrl=url,
                AttributeNames=[
                    'QueueArn', 'VisibilityTimeout', 'DelaySeconds',
                    'MaximumMessageSize', 'MessageRetentionPeriod', 'FifoQueue',
                ],
            ).get('Attributes', {})
        except ClientError:
            attrs = {}
        arn = attrs.get('QueueArn', url)
        results.append({
            'id':                         arn,
            'arn':                        arn,
            'name':                       url.rstrip('/').split('/')[-1],
            'url':                        url,
            'visibility_timeout_seconds': attrs.get('VisibilityTimeout', ''),
            'delay_seconds':              attrs.get('DelaySeconds', ''),
            'max_message_size':           attrs.get('MaximumMessageSize', ''),
            'message_retention_seconds':  attrs.get('MessageRetentionPeriod', ''),
            'fifo_queue':                 attrs.get('FifoQueue', 'false'),
            '_resource_type':             'aws_sqs_queue',
            '_scan_source':               'boto3',
        })
    return results


def scan_apigatewayv2(session):
    api = session.client('apigatewayv2')
    results = []
    for a in api.get_apis().get('Items', []):
        results.append({
            'id':                         a['ApiId'],
            'name':                       a.get('Name', a['ApiId']),
            'protocol_type':              a.get('ProtocolType', ''),
            'api_endpoint':               a.get('ApiEndpoint', ''),
            'route_selection_expression': a.get('RouteSelectionExpression', ''),
            '_resource_type':             'aws_apigatewayv2_api',
            '_scan_source':               'boto3',
        })
    return results


def _iso(dt):
    """boto3 datetime → ISO string (JSON-safe), or '' if absent."""
    return dt.isoformat() if dt else ''


def scan_secrets(session):
    """
    Secrets Manager secrets.

    ListSecrets already returns the rotation metadata (RotationEnabled,
    LastRotatedDate, NextRotationDate) — no DescribeSecret needed — which lets
    the drift-risk plugin flag a rotation that *should* have happened but
    didn't. We deliberately capture no secret value; this is metadata only.
    """
    sm = session.client('secretsmanager')
    results = []
    paginator = sm.get_paginator('list_secrets')
    for page in paginator.paginate():
        for s in page.get('SecretList', []):
            arn = s['ARN']
            rules_cfg = s.get('RotationRules') or {}
            results.append({
                'id':                       arn,
                'arn':                      arn,
                'name':                     s.get('Name', arn.split(':')[-1]),
                'rotation_enabled':         s.get('RotationEnabled', False),
                'rotation_lambda_arn':      s.get('RotationLambdaARN', ''),
                'rotation_interval_days':   rules_cfg.get('AutomaticallyAfterDays', ''),
                'last_rotated_date':        _iso(s.get('LastRotatedDate')),
                'next_rotation_date':       _iso(s.get('NextRotationDate')),
                'last_changed_date':        _iso(s.get('LastChangedDate')),
                'created_date':             _iso(s.get('CreatedDate')),
                '_resource_type':           'aws_secretsmanager_secret',
                '_scan_source':             'boto3',
            })
    return results


# ── Global services (region-agnostic — scanned once) ───────────────────────

def scan_cloudfront(session):
    cf = session.client('cloudfront')
    results = []
    paginator = cf.get_paginator('list_distributions')
    for page in paginator.paginate():
        dl = page.get('DistributionList') or {}
        for d in dl.get('Items') or []:
            results.append({
                'id':              d['Id'],
                'arn':             d.get('ARN', ''),
                'domain_name':     d.get('DomainName', ''),
                'enabled':         d.get('Enabled', False),
                'status':          d.get('Status', ''),
                'price_class':     d.get('PriceClass', ''),
                'comment':         d.get('Comment', ''),
                '_resource_type':  'aws_cloudfront_distribution',
                '_scan_source':    'boto3',
            })
    return results


def scan_route53(session):
    r53 = session.client('route53')
    results = []
    paginator = r53.get_paginator('list_hosted_zones')
    for page in paginator.paginate():
        for z in page.get('HostedZones', []):
            config = z.get('Config') or {}
            results.append({
                'id':             z['Id'].split('/')[-1],
                'name':           z.get('Name', ''),
                'private_zone':   config.get('PrivateZone', False),
                'comment':        config.get('Comment', ''),
                'record_count':   z.get('ResourceRecordSetCount', 0),
                '_resource_type': 'aws_route53_zone',
                '_scan_source':   'boto3',
            })
    return results


# ---------------------------------------------------------------------------
# Scanner registry  (resource_type → scanner function)
# ---------------------------------------------------------------------------

# Regional services — scanned in every configured region.
SCANNERS = [
    ('aws_instance',            scan_ec2),
    ('aws_db_instance',         scan_rds),
    ('aws_ecs_service',         scan_ecs_services),
    ('aws_s3_bucket',           scan_s3),
    ('aws_lb',                  scan_alb),
    ('aws_vpc',                 scan_vpc),
    ('aws_ebs_volume',          scan_ebs),
    ('aws_lambda_function',     scan_lambda),
    ('aws_dynamodb_table',      scan_dynamodb),
    ('aws_elasticache_cluster', scan_elasticache),
    ('aws_efs_file_system',     scan_efs),
    ('aws_eks_cluster',         scan_eks),
    ('aws_sns_topic',           scan_sns),
    ('aws_sqs_queue',           scan_sqs),
    ('aws_apigatewayv2_api',    scan_apigatewayv2),
    ('aws_secretsmanager_secret', scan_secrets),
]

# Global services — scanned once (not per-region) to avoid duplicate churn.
GLOBAL_SCANNERS = [
    ('aws_cloudfront_distribution', scan_cloudfront),
    ('aws_route53_zone',            scan_route53),
]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def _upsert_asset(environment, region, attrs, result):
    """Normalize one scanned attribute dict into an Asset (create or update)."""
    cloud_id = attrs.get('id') or attrs.get('arn', '')
    if not cloud_id:
        return

    result['scanned'] += 1
    resource_type = attrs.get('_resource_type', '')
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
            except Exception as e:
                # A single service failing must not abort the whole scan.
                result['errors'].append(f"{region}/{resource_type}: {e}")
                continue

            for attrs in items:
                _upsert_asset(environment, region, attrs, result)

    # Global services — scanned once via a us-east-1 session.
    try:
        gsession = get_session(system.aws_role_arn, 'us-east-1')
        for resource_type, scanner_fn in GLOBAL_SCANNERS:
            try:
                items = scanner_fn(gsession)
            except Exception as e:
                result['errors'].append(f"global/{resource_type}: {e}")
                continue
            for attrs in items:
                _upsert_asset(environment, 'global', attrs, result)
    except Exception as e:
        result['errors'].append(f"global: session error — {e}")

    return result
