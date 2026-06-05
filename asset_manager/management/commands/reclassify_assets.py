from django.core.management.base import BaseCommand
from asset_manager.models import Asset
from asset_manager.views import ASSET_CATEGORY_MAP

ASSET_TYPE_MAP = {
    'aws_vpc':              Asset.AssetType.VPC,
    'aws_instance':         Asset.AssetType.EC2,
    'aws_db_instance':      Asset.AssetType.RDS,
    'aws_rds_cluster':      Asset.AssetType.AURORA,
    'aws_lb':               Asset.AssetType.ALB,
    'aws_s3_bucket':        Asset.AssetType.S3,
    'aws_ebs_volume':       Asset.AssetType.EBS,
    'aws_lb_target_group':  Asset.AssetType.TARGET_GROUP,
    'aws_lb_listener':      Asset.AssetType.LISTENER,
    'aws_ecs_service':      Asset.AssetType.ECS,
}


class Command(BaseCommand):
    help = 'asset_type=OTHER のAssetをraw_data._resource_typeを元に再分類する'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='更新せず件数だけ表示する')

    def handle(self, *args, **options):
        qs = Asset.objects.filter(asset_type=Asset.AssetType.OTHER)
        total = qs.count()
        self.stdout.write(f'asset_type=OTHER のレコード: {total} 件')

        updated = 0
        skipped = 0
        for asset in qs:
            resource_type = (asset.raw_data or {}).get('_resource_type')
            new_type = ASSET_TYPE_MAP.get(resource_type)
            # FARGATE 判定
            if resource_type == 'aws_ecs_service':
                launch = str((asset.raw_data or {}).get('launch_type', '')).upper()
                if launch == 'FARGATE':
                    new_type = Asset.AssetType.FARGATE
            if new_type:
                new_category = ASSET_CATEGORY_MAP.get(new_type, Asset.AssetCategory.OTHER)
                self.stdout.write(f'  [{resource_type}] {asset.name} → {new_type} ({new_category})')
                if not options['dry_run']:
                    asset.asset_type     = new_type
                    asset.asset_category = new_category
                    asset.save(update_fields=['asset_type', 'asset_category'])
                updated += 1
            else:
                skipped += 1

        label = '(dry-run) ' if options['dry_run'] else ''
        self.stdout.write(self.style.SUCCESS(
            f'{label}再分類: {updated} 件 / スキップ(マップ未登録): {skipped} 件'
        ))
