"""
0011_asset_drift_fields
-----------------------
Drift tracking: add raw_data_prev + last_imported_at to Asset.
Backfill: set raw_data_prev = raw_data for all existing assets so they
start as UNCHANGED on the next import rather than appearing as ADDED.
"""

from django.db import migrations, models
from django.utils import timezone


def backfill_raw_data_prev(apps, schema_editor):
    Asset = apps.get_model('asset_manager', 'Asset')
    now = timezone.now()
    # 既存レコードは raw_data_prev = raw_data でバックフィル
    # → 次回インポートまでは UNCHANGED 扱いになる
    batch = []
    for asset in Asset.objects.all().iterator(chunk_size=500):
        asset.raw_data_prev = asset.raw_data or {}
        asset.last_imported_at = now
        batch.append(asset)
        if len(batch) >= 500:
            Asset.objects.bulk_update(batch, ['raw_data_prev', 'last_imported_at'])
            batch = []
    if batch:
        Asset.objects.bulk_update(batch, ['raw_data_prev', 'last_imported_at'])


class Migration(migrations.Migration):

    dependencies = [
        ('asset_manager', '0010_asset_free_form_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='asset',
            name='raw_data_prev',
            field=models.JSONField(blank=True, default=dict, verbose_name='前回RAWデータ'),
        ),
        migrations.AddField(
            model_name='asset',
            name='last_imported_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='最終インポート日時'),
        ),
        migrations.RunPython(backfill_raw_data_prev, migrations.RunPython.noop),
    ]
