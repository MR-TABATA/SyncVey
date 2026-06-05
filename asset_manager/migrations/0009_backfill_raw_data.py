# データマイグレーション: raw_data が null の Asset を {} に統一する
# Detail テーブル廃止後、raw_data は唯一の属性ストアなので null は不可。

from django.db import migrations


def backfill_raw_data(apps, schema_editor):
    Asset = apps.get_model('asset_manager', 'Asset')
    updated = Asset.objects.filter(raw_data__isnull=True).update(raw_data={})
    if updated:
        print(f'  backfilled raw_data for {updated} asset(s)')


class Migration(migrations.Migration):

    dependencies = [
        ('asset_manager', '0008_drop_detail_tables'),
    ]

    operations = [
        migrations.RunPython(backfill_raw_data, migrations.RunPython.noop),
    ]
