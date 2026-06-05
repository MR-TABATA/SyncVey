# 手動マイグレーション: Detail テーブルを一括削除
# PostgreSQL / MySQL 両対応版
# DROP TABLE IF EXISTS ... CASCADE で FK 制約ごと削除する。

from django.db import migrations


TABLES = [
    # FK 依存順に削除（子 → 親）
    'asset_manager_listener',
    'asset_manager_targetgroup',
    'asset_manager_albdetail',
    'asset_manager_ec2detail',
    'asset_manager_rdsdetail',
    'asset_manager_s3detail',
    'asset_manager_ebsdetail',
    'asset_manager_vpcdetail',
    'asset_manager_ecsdetail',
]


def drop_tables(apps, schema_editor):
    vendor = schema_editor.connection.vendor  # 'postgresql' | 'mysql' | 'sqlite'
    with schema_editor.connection.cursor() as cursor:
        if vendor == 'mysql':
            cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
            for table in TABLES:
                cursor.execute(f"DROP TABLE IF EXISTS `{table}`")
            cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
        elif vendor == 'postgresql':
            for table in TABLES:
                cursor.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')
        else:
            # SQLite: CASCADE 非対応。FK制約はデフォルト無効なので単純に削除
            for table in TABLES:
                cursor.execute(f'DROP TABLE IF EXISTS "{table}"')


def noop(apps, schema_editor):
    """ロールバックは不要（テーブルを再作成しない）。"""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('asset_manager', '0007_auditlog'),
    ]

    operations = [
        migrations.RunPython(drop_tables, noop),
    ]
