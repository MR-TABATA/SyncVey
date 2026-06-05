from django.conf import settings
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('asset_manager', '0005_customuser_organization_membership_system_organization'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='UserProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('note', models.TextField(blank=True, null=True, help_text='管理用備考')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='作成日時')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新日時')),
                ('two_factor_enabled', models.BooleanField(default=False, verbose_name='2段階認証')),
                ('totp_secret', models.CharField(blank=True, max_length=64, verbose_name='TOTPシークレット')),
                ('user', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='profile',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='ユーザー',
                )),
            ],
            options={
                'verbose_name': 'ユーザープロフィール',
                'verbose_name_plural': 'ユーザープロフィール一覧',
            },
        ),
    ]
