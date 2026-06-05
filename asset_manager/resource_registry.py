"""
resource_registry.py
--------------------
新しいクラウドサービス・プロバイダーを追加するときは
このファイルだけを編集する。models.py / migration は不要。

構成:
  PROVIDER_MAP          terraform prefix → provider コード
  RESOURCE_TYPE_MAP     terraform resource_type → (asset_type, category)
  PRIMARY_ASSET_TYPES   サイドバー・ダッシュボードに表示するタイプ一覧
  ICON_MAP              provider × asset_type → static アイコンパス
  CATEGORY_LABELS       カテゴリコード → 表示名（フォーム用）
  PROVIDER_LABELS       プロバイダーコード → 表示名（フォーム用）
  resolve_resource_type()  tfstate リソース → (asset_type, category) 解決関数
"""

from django.utils.translation import gettext_lazy as _

# ---------------------------------------------------------------------------
# プロバイダーマップ  terraform prefix → provider コード
# ---------------------------------------------------------------------------

PROVIDER_MAP = {
    'aws': 'AWS',
}

# フォーム・管理画面用の表示名
PROVIDER_LABELS = {
    'AWS':   'AWS',
    'OTHER': 'Other',
}

# ---------------------------------------------------------------------------
# カテゴリ表示名（フォーム・管理画面用）
# ---------------------------------------------------------------------------

CATEGORY_LABELS = {
    'COMPUTE':   _('Compute'),
    'STORAGE':   _('Storage'),
    'NETWORK':   _('Network'),
    'DATABASE':  _('Database'),
    'ANALYTICS': _('Analytics'),
    'AI_ML':     _('AI / ML'),
    'SECURITY':  _('Security'),
    'OTHER':     _('Other'),
}

# ---------------------------------------------------------------------------
# メインマッピングテーブル
# terraform resource_type → (asset_type_code, category_code)
# ここに1行追加するだけで新サービスに対応できる
# ---------------------------------------------------------------------------

RESOURCE_TYPE_MAP = {

    # =========================================================================
    # AWS
    # =========================================================================

    # ── AWS Compute ───────────────────────────────────────────────────────────
    'aws_instance':                              ('EC2',          'COMPUTE'),
    'aws_ecs_service':                           ('ECS',          'COMPUTE'),
    'aws_ecs_task_definition':                   ('ECS',          'COMPUTE'),
    'aws_lambda_function':                       ('LAMBDA',       'COMPUTE'),
    'aws_eks_cluster':                           ('EKS',          'COMPUTE'),
    'aws_eks_node_group':                        ('EKS',          'COMPUTE'),
    'aws_batch_job_definition':                  ('BATCH',        'COMPUTE'),
    'aws_apprunner_service':                     ('APPRUNNER',    'COMPUTE'),
    'aws_lightsail_instance':                    ('LIGHTSAIL',    'COMPUTE'),
    # ── AWS Database ──────────────────────────────────────────────────────────
    'aws_db_instance':                           ('RDS',          'DATABASE'),
    'aws_rds_cluster':                           ('AURORA',       'DATABASE'),
    'aws_dynamodb_table':                        ('DYNAMODB',     'DATABASE'),
    'aws_elasticache_cluster':                   ('ELASTICACHE',  'DATABASE'),
    'aws_elasticache_replication_group':         ('ELASTICACHE',  'DATABASE'),
    'aws_redshift_cluster':                      ('REDSHIFT',     'DATABASE'),
    'aws_opensearch_domain':                     ('OPENSEARCH',   'DATABASE'),
    'aws_docdb_cluster':                         ('DOCDB',        'DATABASE'),
    'aws_neptune_cluster':                       ('NEPTUNE',      'DATABASE'),
    # ── AWS Analytics ─────────────────────────────────────────────────────────
    'aws_athena_workgroup':                      ('ATHENA',       'ANALYTICS'),
    'aws_athena_database':                       ('ATHENA',       'ANALYTICS'),
    'aws_glue_catalog_database':                 ('GLUE',         'ANALYTICS'),
    'aws_glue_job':                              ('GLUE',         'ANALYTICS'),
    'aws_glue_crawler':                          ('GLUE',         'ANALYTICS'),
    'aws_kinesis_stream':                        ('KINESIS',      'ANALYTICS'),
    'aws_kinesis_firehose_delivery_stream':      ('FIREHOSE',     'ANALYTICS'),
    'aws_sns_topic':                             ('SNS',          'ANALYTICS'),
    'aws_sqs_queue':                             ('SQS',          'ANALYTICS'),
    'aws_emr_cluster':                           ('EMR',          'ANALYTICS'),
    'aws_quicksight_data_source':                ('QUICKSIGHT',   'ANALYTICS'),
    'aws_msk_cluster':                           ('MSK',          'ANALYTICS'),
    'aws_lakeformation_resource':                ('LAKEFORMATION','ANALYTICS'),
    # ── AWS AI / ML ───────────────────────────────────────────────────────────
    'aws_bedrock_agent':                         ('BEDROCK',      'AI_ML'),
    'aws_bedrockagent_agent':                    ('BEDROCK',      'AI_ML'),
    'aws_bedrockagent_knowledge_base':           ('BEDROCK',      'AI_ML'),
    'aws_bedrock_model_invocation_logging_configuration': ('BEDROCK', 'AI_ML'),
    'aws_sagemaker_endpoint':                    ('SAGEMAKER',    'AI_ML'),
    'aws_sagemaker_model':                       ('SAGEMAKER',    'AI_ML'),
    'aws_sagemaker_notebook_instance':           ('SAGEMAKER',    'AI_ML'),
    'aws_sagemaker_domain':                      ('SAGEMAKER',    'AI_ML'),
    'aws_comprehend_document_classifier':        ('COMPREHEND',   'AI_ML'),
    'aws_rekognition_collection':                ('REKOGNITION',  'AI_ML'),
    # ── AWS Storage ───────────────────────────────────────────────────────────
    'aws_s3_bucket':                             ('S3',           'STORAGE'),
    'aws_ebs_volume':                            ('EBS',          'STORAGE'),
    'aws_efs_file_system':                       ('EFS',          'STORAGE'),
    'aws_fsx_lustre_file_system':                ('FSX',          'STORAGE'),
    'aws_fsx_windows_file_system':               ('FSX',          'STORAGE'),
    'aws_backup_vault':                          ('BACKUP',       'STORAGE'),
    'aws_glacier_vault':                         ('GLACIER',      'STORAGE'),
    # ── AWS Network ───────────────────────────────────────────────────────────
    'aws_vpc':                                   ('VPC',          'NETWORK'),
    'aws_lb':                                    ('ALB',          'NETWORK'),
    'aws_lb_target_group':                       ('TG',           'NETWORK'),
    'aws_lb_listener':                           ('LISTENER',     'NETWORK'),
    'aws_cloudfront_distribution':               ('CLOUDFRONT',   'NETWORK'),
    'aws_route53_zone':                          ('ROUTE53',      'NETWORK'),
    'aws_route53_record':                        ('ROUTE53',      'NETWORK'),
    'aws_api_gateway_rest_api':                  ('API_GW',       'NETWORK'),
    'aws_apigatewayv2_api':                      ('API_GW',       'NETWORK'),
    'aws_nat_gateway':                           ('NAT_GW',       'NETWORK'),
    'aws_vpc_endpoint':                          ('VPC_EP',       'NETWORK'),
    # ── AWS Security ──────────────────────────────────────────────────────────
    'aws_wafv2_web_acl':                         ('WAF',          'SECURITY'),
    'aws_kms_key':                               ('KMS',          'SECURITY'),
    'aws_secretsmanager_secret':                 ('SECRETS_MGR',  'SECURITY'),
    'aws_iam_role':                              ('IAM',          'SECURITY'),
    'aws_guardduty_detector':                    ('GUARDDUTY',    'SECURITY'),
    'aws_shield_protection':                     ('SHIELD',       'SECURITY'),

}

# ---------------------------------------------------------------------------
# カテゴリ判定キーワード（RESOURCE_TYPE_MAP にない resource_type へのフォールバック）
# ---------------------------------------------------------------------------

_CATEGORY_KEYWORDS = {
    'COMPUTE':   {'instance', 'lambda', 'ecs', 'eks', 'fargate',
                  'batch', 'apprunner', 'lightsail'},
    'DATABASE':  {'db', 'rds', 'dynamodb', 'elasticache', 'redshift', 'aurora',
                  'docdb', 'neptune', 'keyspaces', 'timestream'},
    'STORAGE':   {'s3', 'ebs', 'efs', 'fsx', 'volume', 'bucket',
                  'backup', 'glacier'},
    'NETWORK':   {'vpc', 'subnet', 'lb', 'alb', 'nlb', 'route', 'gateway',
                  'cloudfront', 'api_gateway', 'apigateway', 'nat', 'transit',
                  'zone', 'dns'},
    'AI_ML':     {'bedrock', 'sagemaker', 'comprehend', 'rekognition', 'textract',
                  'translate', 'polly', 'lex', 'forecast', 'personalize', 'kendra'},
    'ANALYTICS': {'athena', 'glue', 'kinesis', 'firehose', 'emr', 'quicksight',
                  'msk', 'lakeformation', 'sns', 'sqs'},
    'SECURITY':  {'waf', 'kms', 'secret', 'iam', 'shield', 'guardduty',
                  'securityhub', 'inspector', 'macie', 'acm'},
}

# ---------------------------------------------------------------------------
# サイドバー・ダッシュボードに表示する「主要タイプ」
# 新タイプをサイドバーに載せたい場合はここに追加するだけ
# ---------------------------------------------------------------------------

PRIMARY_ASSET_TYPES = [
    # ── Compute ──────────────────────────────────────────
    'EC2', 'ECS', 'FARGATE', 'LAMBDA', 'EKS',
    # ── Database ─────────────────────────────────────────
    'RDS', 'AURORA', 'DYNAMODB', 'ELASTICACHE', 'REDSHIFT',
    # ── Analytics ────────────────────────────────────────
    'ATHENA', 'GLUE', 'KINESIS', 'SNS', 'SQS',
    # ── AI/ML ────────────────────────────────────────────
    'BEDROCK', 'SAGEMAKER',
    # ── Storage ──────────────────────────────────────────
    'S3', 'EBS', 'EFS',
    # ── Network ──────────────────────────────────────────
    'VPC', 'ALB', 'CLOUDFRONT', 'API_GW',
    # ── Security ─────────────────────────────────────────
    'WAF', 'KMS',
]

# ---------------------------------------------------------------------------
# アイコンマップ（provider × asset_type → static 相対パス）
# static/ 以下の相対パスを指定する
# 対応アイコンがない場合は登録不要 → テンプレートでデフォルトアイコンを表示
# ---------------------------------------------------------------------------

ICON_MAP = {
    'AWS': {
        # ── Compute ──────────────────────────────────────────────────────────
        'EC2':          'cloud-icons/aws/ec2.svg',
        'ECS':          'cloud-icons/aws/ecs.svg',
        'FARGATE':      'cloud-icons/aws/fargate.svg',
        'LAMBDA':       'cloud-icons/aws/lambda.svg',
        'EKS':          'cloud-icons/aws/eks.svg',
        # ── Database ─────────────────────────────────────────────────────────
        'RDS':          'cloud-icons/aws/rds.svg',
        'AURORA':       'cloud-icons/aws/aurora.svg',
        'DYNAMODB':     'cloud-icons/aws/dynamodb.svg',
        'ELASTICACHE':  'cloud-icons/aws/elasticache.svg',
        'REDSHIFT':     'cloud-icons/aws/redshift.svg',
        # ── Storage ──────────────────────────────────────────────────────────
        'S3':           'cloud-icons/aws/s3.svg',
        'EBS':          'cloud-icons/aws/ebs.svg',
        # ── Network ──────────────────────────────────────────────────────────
        'VPC':          'cloud-icons/aws/vpc.svg',
        'ALB':          'cloud-icons/aws/alb.svg',
        'CLOUDFRONT':   'cloud-icons/aws/cloudfront.svg',
        'API_GW':       'cloud-icons/aws/api_gw.svg',
        'NAT_GW':       'cloud-icons/aws/nat_gw.svg',
        'ROUTE53':      'cloud-icons/aws/route53.svg',
        # ── Analytics ────────────────────────────────────────────────────────
        'ATHENA':       'cloud-icons/aws/athena.svg',
        'GLUE':         'cloud-icons/aws/glue.svg',
        'KINESIS':      'cloud-icons/aws/kinesis.svg',
        # ── AI / ML ──────────────────────────────────────────────────────────
        'BEDROCK':      'cloud-icons/aws/bedrock.svg',
        'SAGEMAKER':    'cloud-icons/aws/sagemaker.svg',
        # ── Messaging ────────────────────────────────────────────────────────
        'SNS':          'cloud-icons/aws/sns.svg',
        'SQS':          'cloud-icons/aws/sqs.svg',
        # ── Security ─────────────────────────────────────────────────────────
        'WAF':          'cloud-icons/aws/waf.svg',
        'KMS':          'cloud-icons/aws/kms.svg',
        'COGNITO':      'cloud-icons/aws/cognito.svg',
        # ── Observability ────────────────────────────────────────────────────
        'CLOUDWATCH':   'cloud-icons/aws/cloudwatch.svg',
    },
}


def get_icon(provider, asset_type):
    """asset_type に対応する static 相対パスを返す。なければ None。"""
    return ICON_MAP.get(provider, {}).get(asset_type)


# ---------------------------------------------------------------------------
# 解決関数（tfstate インポートから呼ばれる）
# ---------------------------------------------------------------------------

def resolve_resource_type(terraform_resource_type, attributes=None):
    """
    terraform resource_type → (asset_type_code, category_code) を返す。

    優先順:
      1. RESOURCE_TYPE_MAP の直接マッチ
      2. カテゴリキーワードマッチ + resource_type 名から asset_type を自動生成
      3. ('OTHER', 'OTHER')

    ECS Fargate 判定（launch_type == 'FARGATE'）もここで実施。
    """
    attributes = attributes or {}

    if terraform_resource_type in RESOURCE_TYPE_MAP:
        asset_type, category = RESOURCE_TYPE_MAP[terraform_resource_type]
    else:
        asset_type = _derive_asset_type(terraform_resource_type)
        category   = _derive_category(terraform_resource_type)

    # ECS Fargate 判定
    if terraform_resource_type == 'aws_ecs_service':
        if str(attributes.get('launch_type', '')).upper() == 'FARGATE':
            asset_type = 'FARGATE'

    return asset_type, category


def resolve_provider(terraform_resource_type):
    """terraform resource_type の prefix からプロバイダーコードを返す。"""
    prefix = terraform_resource_type.split('_')[0]
    return PROVIDER_MAP.get(prefix, 'OTHER')


def _derive_asset_type(resource_type):
    """aws_foo_bar → FOO_BAR（プロバイダー prefix を除去して大文字化）"""
    parts = resource_type.split('_')
    if parts[0] in PROVIDER_MAP:
        parts = parts[1:]
    return '_'.join(parts).upper()[:50]


def _derive_category(resource_type):
    lower = resource_type.lower()
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return category
    return 'OTHER'


# ---------------------------------------------------------------------------
# フォーム用ヘルパー
# ---------------------------------------------------------------------------

def get_provider_choices():
    """フォーム用: [(code, label), ...] を返す。"""
    return [(code, label) for code, label in PROVIDER_LABELS.items()]


def get_category_choices():
    """フォーム用: [(code, label), ...] を返す。"""
    return [(code, label) for code, label in CATEGORY_LABELS.items()]


def get_known_asset_types():
    """
    フォーム用: RESOURCE_TYPE_MAP の重複排除 asset_type を
    PRIMARY_ASSET_TYPES 順 → アルファベット順で返す。
    """
    seen = {asset_type for asset_type, _ in RESOURCE_TYPE_MAP.values()}
    ordered = [t for t in PRIMARY_ASSET_TYPES if t in seen]
    ordered += sorted(t for t in seen if t not in PRIMARY_ASSET_TYPES)
    ordered.append('OTHER')
    return [(t, t) for t in ordered]
