# モデル変更マイグレーション: TextChoices → 自由文字列 CharField
# asset_type / asset_category / provider から choices 制約を撤廃し、
# asset_type の max_length を 20 → 50 に拡張する。

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('asset_manager', '0009_backfill_raw_data'),
    ]

    operations = [
        migrations.AlterField(
            model_name='asset',
            name='provider',
            field=models.CharField(
                default='AWS',
                max_length=20,
                verbose_name='プロバイダー',
            ),
        ),
        migrations.AlterField(
            model_name='asset',
            name='asset_type',
            field=models.CharField(
                max_length=50,
                verbose_name='資産タイプ',
            ),
        ),
        migrations.AlterField(
            model_name='asset',
            name='asset_category',
            field=models.CharField(
                db_index=True,
                default='OTHER',
                max_length=20,
                verbose_name='資産カテゴリ',
            ),
        ),
    ]
