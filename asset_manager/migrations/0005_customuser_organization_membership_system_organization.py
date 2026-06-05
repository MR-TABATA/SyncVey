from django.conf import settings
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('asset_manager', '0004_asset_category_fargate_aurora'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # 1. Organization
        migrations.CreateModel(
            name='Organization',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('note', models.TextField(blank=True, null=True, help_text='管理用備考')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='作成日時')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新日時')),
                ('name', models.CharField(max_length=255, unique=True, verbose_name='法人名')),
                ('slug', models.SlugField(unique=True, verbose_name='スラッグ')),
            ],
            options={
                'verbose_name': '法人',
                'verbose_name_plural': '法人一覧',
                'ordering': ['name'],
            },
        ),
        # 2. Membership
        migrations.CreateModel(
            name='Membership',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('note', models.TextField(blank=True, null=True, help_text='管理用備考')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='作成日時')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新日時')),
                ('role', models.CharField(
                    choices=[
                        ('OWNER', 'オーナー'),
                        ('INFRA_ADMIN', 'インフラ管理者'),
                        ('APP_ADMIN', 'アプリ管理者'),
                        ('VIEWER', '閲覧者'),
                    ],
                    default='VIEWER',
                    max_length=20,
                    verbose_name='ロール',
                )),
                ('organization', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='memberships',
                    to='asset_manager.organization',
                    verbose_name='法人',
                )),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='memberships',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='ユーザー',
                )),
            ],
            options={
                'verbose_name': 'メンバーシップ',
                'verbose_name_plural': 'メンバーシップ一覧',
                'unique_together': {('user', 'organization')},
            },
        ),
        # 3. System.organization FK
        migrations.AddField(
            model_name='system',
            name='organization',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='systems',
                to='asset_manager.organization',
                verbose_name='法人',
            ),
        ),
    ]
