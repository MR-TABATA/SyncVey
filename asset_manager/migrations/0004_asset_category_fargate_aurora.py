from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('asset_manager', '0003_alter_asset_asset_type'),
    ]

    operations = [
        # 1. asset_type の choices を更新（AURORA, FARGATE 追加）
        migrations.AlterField(
            model_name='asset',
            name='asset_type',
            field=models.CharField(
                choices=[
                    ('EC2',      'EC2 Instance'),
                    ('RDS',      'RDS Instance'),
                    ('AURORA',   'Aurora Cluster'),
                    ('ALB',      'Application Load Balancer'),
                    ('VPC',      'VPC'),
                    ('S3',       'S3 Bucket'),
                    ('EBS',      'EBS Volume'),
                    ('ECS',      'ECS Service'),
                    ('FARGATE',  'ECS Fargate'),
                    ('TG',       'Target Group'),
                    ('LISTENER', 'Listener'),
                    ('OTHER',    'Other'),
                ],
                max_length=20,
                verbose_name='資産タイプ',
            ),
        ),
        # 2. asset_category フィールドを追加
        migrations.AddField(
            model_name='asset',
            name='asset_category',
            field=models.CharField(
                choices=[
                    ('COMPUTE',  'コンピューティング'),
                    ('STORAGE',  'ストレージ'),
                    ('NETWORK',  'ネットワーク'),
                    ('DATABASE', 'データベース'),
                    ('OTHER',    'その他'),
                ],
                default='OTHER',
                db_index=True,
                max_length=20,
                verbose_name='資産カテゴリ',
            ),
        ),
        # 3. 既存レコードをカテゴリに振り分けるデータマイグレーション
        migrations.RunSQL(
            sql="""
            UPDATE asset_manager_asset SET asset_category =
              CASE asset_type
                WHEN 'EC2'     THEN 'COMPUTE'
                WHEN 'ECS'     THEN 'COMPUTE'
                WHEN 'FARGATE' THEN 'COMPUTE'
                WHEN 'EBS'     THEN 'STORAGE'
                WHEN 'S3'      THEN 'STORAGE'
                WHEN 'ALB'     THEN 'NETWORK'
                WHEN 'VPC'     THEN 'NETWORK'
                WHEN 'RDS'     THEN 'DATABASE'
                WHEN 'AURORA'  THEN 'DATABASE'
                ELSE 'OTHER'
              END;
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
