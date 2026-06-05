"""
0012_system_scan_schedule
-------------------------
1. System モデルに scan_enabled / scan_interval_minutes を追加（実際のDB操作）
2. asset_detail.py 削除に伴い残っていた古い Detail モデルの migration state を清掃
   - 物理テーブルは 0008 で RunPython DROP 済み → SeparateDatabaseAndState で SQL なし
"""

from django.db import migrations, models


# Detail モデルの削除は state のみ（DB は 0008 で処理済み）
_state_only_deletes = [
    migrations.RemoveField(model_name='listener',    name='alb'),
    migrations.RemoveField(model_name='targetgroup', name='alb'),
    migrations.RemoveField(model_name='ebsdetail',   name='asset'),
    migrations.RemoveField(model_name='ec2detail',   name='asset'),
    migrations.RemoveField(model_name='ecsdetail',   name='asset'),
    migrations.AlterUniqueTogether(name='listener',  unique_together=None),
    migrations.RemoveField(model_name='listener',    name='default_target_group'),
    migrations.RemoveField(model_name='rdsdetail',   name='asset'),
    migrations.RemoveField(model_name='s3detail',    name='asset'),
    migrations.RemoveField(model_name='vpcdetail',   name='asset'),
    migrations.DeleteModel(name='ALBDetail'),
    migrations.DeleteModel(name='EBSDetail'),
    migrations.DeleteModel(name='EC2Detail'),
    migrations.DeleteModel(name='ECSDetail'),
    migrations.DeleteModel(name='Listener'),
    migrations.DeleteModel(name='TargetGroup'),
    migrations.DeleteModel(name='RDSDetail'),
    migrations.DeleteModel(name='S3Detail'),
    migrations.DeleteModel(name='VPCDetail'),
]


class Migration(migrations.Migration):

    dependencies = [
        ('asset_manager', '0011_asset_drift_fields'),
    ]

    operations = [
        # Detail モデルの state 清掃（SQL なし）
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=_state_only_deletes,
        ),

        # System スケジューラーフィールド（実際の ALTER TABLE）
        migrations.AddField(
            model_name='system',
            name='scan_enabled',
            field=models.BooleanField(default=False, verbose_name='自動スキャン'),
        ),
        migrations.AddField(
            model_name='system',
            name='scan_interval_minutes',
            field=models.PositiveIntegerField(default=60, verbose_name='スキャン間隔(分)'),
        ),
    ]
