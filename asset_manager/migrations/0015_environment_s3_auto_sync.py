from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('asset_manager', '0014_environment_s3_backend'),
    ]

    operations = [
        migrations.AddField(
            model_name='environment',
            name='s3_auto_sync',
            field=models.BooleanField(default=False, verbose_name='Auto Sync on Schedule'),
        ),
    ]
