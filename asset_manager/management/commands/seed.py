"""
python manage.py seed             # insert sample data (idempotent)
python manage.py seed --flush     # delete all app data first, then insert
python manage.py seed --flush-users  # also delete seeded users/groups
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from django.db import transaction

from asset_manager.models import (
    AppDependency, AppEnvConfig, Application,
    Asset, Environment,
    Membership, Organization, System,
)

User = get_user_model()

SEEDED_USERS = [
    "tabata_hiroshi", "tanaka_kenji", "yamada_yuki",
    "sato_mai", "suzuki_taro", "demo_viewer",
]
SEEDED_GROUPS = ["Owner", "Infra Admin", "App Admin", "Viewer"]


class Command(BaseCommand):
    help = "Seed the database with sample data."

    def add_arguments(self, parser):
        parser.add_argument("--flush",       action="store_true")
        parser.add_argument("--flush-users", action="store_true")

    def handle(self, *args, **options):
        if options["flush"]:
            self._flush()
        if options["flush_users"]:
            self._flush_users()
        with transaction.atomic():
            self._seed()
            self._seed_users()
        self.stdout.write(self.style.SUCCESS("Seed complete."))

    # ------------------------------------------------------------------
    # flush
    # ------------------------------------------------------------------

    def _flush(self):
        for model in [AppDependency, AppEnvConfig, Application,
                      Asset, Environment, System, Membership, Organization]:
            count, _ = model.objects.all().delete()
            self.stdout.write(f"  deleted {count:>4}  {model.__name__}")

    def _flush_users(self):
        count, _ = User.objects.filter(username__in=SEEDED_USERS).delete()
        self.stdout.write(f"  deleted {count:>4}  User (seeded)")
        count, _ = Group.objects.filter(name__in=SEEDED_GROUPS).delete()
        self.stdout.write(f"  deleted {count:>4}  Group (seeded)")

    # ------------------------------------------------------------------
    # seed
    # ------------------------------------------------------------------

    def _seed(self):
        # ── Organizations ─────────────────────────────────────────────
        org_a, _ = Organization.objects.get_or_create(
            slug="arcana",
            defaults={"name": "Arcana Inc."},
        )
        org_b, _ = Organization.objects.get_or_create(
            slug="demo-corp",
            defaults={"name": "Demo Corp"},
        )
        self.stdout.write("  organizations OK")

        # ── Systems ───────────────────────────────────────────────────
        ec_sys, _ = System.objects.get_or_create(
            code="ecsite",
            defaults={"name": "E-Commerce", "aws_scan_regions": ["ap-northeast-1"], "organization": org_a},
        )
        cms_sys, _ = System.objects.get_or_create(
            code="cms",
            defaults={"name": "Internal CMS", "aws_scan_regions": ["ap-northeast-1", "us-east-1"], "organization": org_a},
        )
        demo_sys, _ = System.objects.get_or_create(
            code="demo",
            defaults={"name": "Demo System", "aws_scan_regions": ["ap-northeast-1"], "organization": org_b},
        )
        self.stdout.write("  systems OK")

        # ── Environments ──────────────────────────────────────────────
        ec_prod, _ = Environment.objects.get_or_create(
            system=ec_sys, name="Production",
            defaults={"env_type": "PROD", "tfstate_filename": "ecsite-prod.tfstate"},
        )
        ec_stg, _ = Environment.objects.get_or_create(
            system=ec_sys, name="Staging",
            defaults={"env_type": "STG", "tfstate_filename": "ecsite-stg.tfstate"},
        )
        ec_dev, _ = Environment.objects.get_or_create(
            system=ec_sys, name="Development",
            defaults={"env_type": "DEV"},
        )
        cms_prod, _ = Environment.objects.get_or_create(
            system=cms_sys, name="Production",
            defaults={"env_type": "PROD"},
        )
        cms_dev, _ = Environment.objects.get_or_create(
            system=cms_sys, name="Development",
            defaults={"env_type": "DEV"},
        )
        Environment.objects.get_or_create(
            system=demo_sys, name="Production",
            defaults={"env_type": "PROD"},
        )
        self.stdout.write("  environments OK")

        # ── Assets（raw_data に詳細を格納）────────────────────────────

        # VPC
        vpc_asset, _ = Asset.objects.get_or_create(
            cloud_id="vpc-0a1b2c3d4e5f00001",
            defaults={
                "name": "ecsite-prod-vpc", "environment": ec_prod,
                "provider": "AWS", "asset_type": "VPC", "asset_category": "NETWORK",
                "region": "ap-northeast-1",
                "raw_data": {
                    "cidr_block": "10.0.0.0/16", "is_default": False,
                    "dns_support": True, "dns_hostnames": True,
                    "dhcp_options_id": "dopt-0123456789abcdef0",
                },
            },
        )

        # EC2 ×2
        ec2_web, _ = Asset.objects.get_or_create(
            cloud_id="i-0a1b2c3d4e5f00001",
            defaults={
                "name": "ecsite-prod-web-01", "environment": ec_prod,
                "provider": "AWS", "asset_type": "EC2", "asset_category": "COMPUTE",
                "region": "ap-northeast-1",
                "raw_data": {
                    "instance_type": "t3.medium", "ami_id": "ami-0d52744d6551d851e",
                    "instance_state": "running", "platform": "linux",
                    "key_name": "ecsite-prod-key",
                    "vpc_id": "vpc-0a1b2c3d4e5f00001",
                    "subnet_id": "subnet-0a1b2c3d4e5f00001",
                    "availability_zone": "ap-northeast-1a",
                    "private_ip": "10.0.1.10", "public_ip": "54.238.10.1",
                    "security_groups": [{"id": "sg-0001", "name": "ecsite-web-sg"}],
                    "iam_instance_profile": "ecsite-prod-web-role",
                    "monitoring_enabled": True,
                },
            },
        )
        ec2_batch, _ = Asset.objects.get_or_create(
            cloud_id="i-0a1b2c3d4e5f00002",
            defaults={
                "name": "ecsite-prod-batch-01", "environment": ec_prod,
                "provider": "AWS", "asset_type": "EC2", "asset_category": "COMPUTE",
                "region": "ap-northeast-1",
                "raw_data": {
                    "instance_type": "c5.large", "ami_id": "ami-0d52744d6551d851e",
                    "instance_state": "running", "platform": "linux",
                    "vpc_id": "vpc-0a1b2c3d4e5f00001",
                    "subnet_id": "subnet-0a1b2c3d4e5f00002",
                    "availability_zone": "ap-northeast-1c",
                    "private_ip": "10.0.2.20",
                    "security_groups": [{"id": "sg-0002", "name": "ecsite-batch-sg"}],
                    "iam_instance_profile": "ecsite-prod-batch-role",
                },
            },
        )

        # EBS
        Asset.objects.get_or_create(
            cloud_id="vol-0a1b2c3d4e5f00001",
            defaults={
                "name": "ecsite-prod-web-root", "environment": ec_prod,
                "provider": "AWS", "asset_type": "EBS", "asset_category": "STORAGE",
                "region": "ap-northeast-1",
                "raw_data": {
                    "type": "gp3", "size": 30, "iops": 3000, "throughput": 125,
                    "availability_zone": "ap-northeast-1a", "encrypted": True,
                    "kms_key_id": "arn:aws:kms:ap-northeast-1:123456789012:key/mrk-aabbcc",
                    "attachments": [{"instance_id": "i-0a1b2c3d4e5f00001", "delete_on_termination": True}],
                },
            },
        )

        # RDS
        rds_asset, _ = Asset.objects.get_or_create(
            cloud_id="ecsite-prod-mysql",
            defaults={
                "name": "ecsite-prod-db", "environment": ec_prod,
                "provider": "AWS", "asset_type": "RDS", "asset_category": "DATABASE",
                "region": "ap-northeast-1",
                "endpoint": "ecsite-prod-mysql.cluster-xxxxxxxxxxxx.ap-northeast-1.rds.amazonaws.com",
                "raw_data": {
                    "engine": "mysql", "engine_version": "8.0.36",
                    "instance_class": "db.t3.medium", "db_name": "ecsite",
                    "multi_az": True, "publicly_accessible": False,
                    "storage_type": "gp3", "allocated_storage": 100,
                    "backup_retention_period": 7, "deletion_protection": True,
                },
            },
        )

        # S3 ×2
        Asset.objects.get_or_create(
            cloud_id="ecsite-prod-static",
            defaults={
                "name": "ecsite-prod-static", "environment": ec_prod,
                "provider": "AWS", "asset_type": "S3", "asset_category": "STORAGE",
                "region": "ap-northeast-1",
                "raw_data": {
                    "versioning": "Disabled", "block_public_acls": False,
                    "server_side_encryption_configuration": {"rules": [{"algorithm": "AES256"}]},
                    "logging": {"target_bucket": "ecsite-prod-logs"},
                },
            },
        )
        Asset.objects.get_or_create(
            cloud_id="ecsite-prod-backup",
            defaults={
                "name": "ecsite-prod-backup", "environment": ec_prod,
                "provider": "AWS", "asset_type": "S3", "asset_category": "STORAGE",
                "region": "ap-northeast-1",
                "raw_data": {
                    "versioning": "Enabled", "block_public_acls": True,
                    "server_side_encryption_configuration": {"rules": [{"algorithm": "aws:kms"}]},
                    "replication_enabled": True, "object_lock_enabled": True,
                },
            },
        )

        # ALB
        alb_asset, _ = Asset.objects.get_or_create(
            cloud_id="arn:aws:elasticloadbalancing:ap-northeast-1:123456789012:loadbalancer/app/ecsite-prod-alb/0123456789abcdef",
            defaults={
                "name": "ecsite-prod-alb", "environment": ec_prod,
                "provider": "AWS", "asset_type": "ALB", "asset_category": "NETWORK",
                "region": "ap-northeast-1",
                "endpoint": "ecsite-prod-alb-123456789.ap-northeast-1.elb.amazonaws.com",
                "raw_data": {
                    "scheme": "internet-facing", "state": {"code": "active"},
                    "vpc_id": "vpc-0a1b2c3d4e5f00001",
                    "subnets": ["subnet-0a1b2c3d4e5f00001", "subnet-0a1b2c3d4e5f00002"],
                    "security_groups": ["sg-0004"],
                    "idle_timeout": {"timeout_seconds": 60},
                    "deletion_protection": {"enabled": True},
                    "access_logs": {"s3": {"bucket": "ecsite-prod-logs", "enabled": True}},
                },
            },
        )

        # ECS (Fargate)
        ecs_asset, _ = Asset.objects.get_or_create(
            cloud_id="arn:aws:ecs:ap-northeast-1:123456789012:service/ecsite-prod-cluster/ecsite-api",
            defaults={
                "name": "ecsite-api", "environment": ec_prod,
                "provider": "AWS", "asset_type": "FARGATE", "asset_category": "COMPUTE",
                "region": "ap-northeast-1",
                "raw_data": {
                    "cluster": "ecsite-prod-cluster", "launch_type": "FARGATE",
                    "task_definition": "ecsite-api:42",
                    "desired_count": 2, "running_count": 2, "pending_count": 0,
                    "status": "ACTIVE", "scheduling_strategy": "REPLICA",
                    "network_configuration": {
                        "awsvpc_configuration": {
                            "subnets": ["subnet-0a1b2c3d4e5f00001"],
                            "security_groups": ["sg-0005"],
                            "assign_public_ip": "DISABLED",
                        }
                    },
                },
            },
        )

        # ── Staging assets ────────────────────────────────────────────
        ec2_stg, _ = Asset.objects.get_or_create(
            cloud_id="i-0a1b2c3d4e5f00010",
            defaults={
                "name": "ecsite-stg-web-01", "environment": ec_stg,
                "provider": "AWS", "asset_type": "EC2", "asset_category": "COMPUTE",
                "region": "ap-northeast-1",
                "raw_data": {
                    "instance_type": "t3.small", "instance_state": "running",
                    "private_ip": "10.0.1.50",
                },
            },
        )
        rds_stg, _ = Asset.objects.get_or_create(
            cloud_id="ecsite-stg-mysql",
            defaults={
                "name": "ecsite-stg-db", "environment": ec_stg,
                "provider": "AWS", "asset_type": "RDS", "asset_category": "DATABASE",
                "region": "ap-northeast-1",
                "raw_data": {
                    "engine": "mysql", "engine_version": "8.0.36",
                    "instance_class": "db.t3.small", "multi_az": False,
                    "allocated_storage": 20, "backup_retention_period": 1,
                },
            },
        )

        # ── CMS prod assets ───────────────────────────────────────────
        Asset.objects.get_or_create(
            cloud_id="vpc-0a1b2c3d4e5f00002",
            defaults={
                "name": "cms-prod-vpc", "environment": cms_prod,
                "provider": "AWS", "asset_type": "VPC", "asset_category": "NETWORK",
                "region": "ap-northeast-1",
                "raw_data": {"cidr_block": "10.1.0.0/16"},
            },
        )
        cms_ec2, _ = Asset.objects.get_or_create(
            cloud_id="i-0a1b2c3d4e5f00020",
            defaults={
                "name": "cms-prod-web-01", "environment": cms_prod,
                "provider": "AWS", "asset_type": "EC2", "asset_category": "COMPUTE",
                "region": "ap-northeast-1",
                "raw_data": {
                    "instance_type": "t3.large", "instance_state": "running",
                    "private_ip": "10.1.1.10", "public_ip": "52.196.20.1",
                },
            },
        )
        cms_rds, _ = Asset.objects.get_or_create(
            cloud_id="cms-prod-postgres",
            defaults={
                "name": "cms-prod-db", "environment": cms_prod,
                "provider": "AWS", "asset_type": "RDS", "asset_category": "DATABASE",
                "region": "ap-northeast-1",
                "raw_data": {
                    "engine": "postgres", "engine_version": "16.2",
                    "instance_class": "db.t3.medium", "multi_az": False,
                    "allocated_storage": 50, "deletion_protection": True,
                },
            },
        )

        self.stdout.write("  assets OK")

        # ── Applications ──────────────────────────────────────────────
        ec_front, _ = Application.objects.get_or_create(
            system=ec_sys, name="ecsite-frontend",
            defaults={
                "language": "typescript", "framework": "nextjs",
                "repository_url": "https://github.com/example/ecsite-frontend",
                "description": "Next.js frontend",
            },
        )
        ec_api, _ = Application.objects.get_or_create(
            system=ec_sys, name="ecsite-api",
            defaults={
                "language": "python", "framework": "django",
                "repository_url": "https://github.com/example/ecsite-api",
                "description": "Django REST API backend",
            },
        )
        cms_app, _ = Application.objects.get_or_create(
            system=cms_sys, name="cms-app",
            defaults={
                "language": "php", "framework": "laravel",
                "repository_url": "https://github.com/example/cms",
                "description": "Laravel CMS",
            },
        )
        self.stdout.write("  applications OK")

        # ── AppEnvConfig ──────────────────────────────────────────────
        front_prod_cfg, _ = AppEnvConfig.objects.get_or_create(
            application=ec_front, environment=ec_prod,
            defaults={
                "language_version": "20.x", "framework_version": "14.2",
                "runtime": "node", "runtime_version": "20.11.0",
                "deploy_target": ecs_asset, "deploy_method": "ecs", "branch": "main",
            },
        )
        api_prod_cfg, _ = AppEnvConfig.objects.get_or_create(
            application=ec_api, environment=ec_prod,
            defaults={
                "language_version": "3.12", "framework_version": "5.1",
                "runtime": "gunicorn", "runtime_version": "21.2.0",
                "db_asset": rds_asset, "deploy_target": ecs_asset,
                "deploy_method": "ecs", "branch": "main",
            },
        )
        api_stg_cfg, _ = AppEnvConfig.objects.get_or_create(
            application=ec_api, environment=ec_stg,
            defaults={
                "language_version": "3.12", "framework_version": "5.1",
                "runtime": "gunicorn", "runtime_version": "21.2.0",
                "db_asset": rds_stg, "deploy_target": ec2_stg,
                "deploy_method": "ec2", "branch": "develop",
            },
        )
        cms_prod_cfg, _ = AppEnvConfig.objects.get_or_create(
            application=cms_app, environment=cms_prod,
            defaults={
                "language_version": "8.3", "framework_version": "11.x",
                "runtime": "nginx", "runtime_version": "1.24",
                "db_asset": cms_rds, "deploy_target": cms_ec2,
                "deploy_method": "ec2", "branch": "main",
            },
        )
        cms_dev_cfg, _ = AppEnvConfig.objects.get_or_create(
            application=cms_app, environment=cms_dev,
            defaults={
                "language_version": "8.0", "framework_version": "10.x",
                "runtime": "nginx", "runtime_version": "1.20",
                "db_asset": cms_rds, "deploy_target": cms_ec2,
                "deploy_method": "ec2", "branch": "develop",
            },
        )
        self.stdout.write("  app env configs OK")

        # ── AppDependency ─────────────────────────────────────────────
        for cfg, deps in [
            (api_prod_cfg, [
                ("Django",              "5.1.4",  "library"),
                ("djangorestframework", "3.15.2", "library"),
                ("celery",              "5.3.6",  "library"),
                ("boto3",               "1.34.0", "library"),
                ("psycopg2-binary",     "2.9.9",  "library"),
                ("Redis",               "7.2",    "middleware"),
                ("nginx",               "1.24",   "middleware"),
            ]),
            (api_stg_cfg, [
                ("Django",          "5.1.4", "library"),
                ("celery",          "5.3.6", "library"),
                ("psycopg2-binary", "2.9.9", "library"),
                ("Redis",           "6.2",   "middleware"),   # EOL
                ("nginx",           "1.20",  "middleware"),   # EOL
                ("Python",          "3.10",  "middleware"),   # warning
            ]),
            (cms_prod_cfg, [
                ("laravel/framework",         "11.9", "library"),
                ("inertiajs/inertia-laravel", "1.0",  "library"),
                ("PHP-FPM",  "8.3",  "middleware"),
                ("Redis",    "7.2",  "middleware"),
                ("nginx",    "1.24", "middleware"),
                ("Composer", "2.7",  "tool"),
            ]),
            (cms_dev_cfg, [
                ("laravel/framework",         "10.0", "library"),
                ("inertiajs/inertia-laravel", "1.0",  "library"),
                ("PHP-FPM",  "8.0",  "middleware"),   # EOL
                ("Redis",    "6.2",  "middleware"),   # EOL
                ("nginx",    "1.20", "middleware"),   # EOL
                ("Composer", "2.7",  "tool"),
            ]),
        ]:
            for name, version, dep_type in deps:
                AppDependency.objects.get_or_create(
                    app_env_config=cfg, name=name,
                    defaults={"version": version, "dep_type": dep_type},
                )

        self.stdout.write("  dependencies OK")

    # ------------------------------------------------------------------
    # users / groups / memberships
    # ------------------------------------------------------------------

    def _seed_users(self):
        org_a = Organization.objects.get(slug="arcana")
        org_b = Organization.objects.get(slug="demo-corp")

        app_models = [
            "organization", "membership", "system", "environment",
            "asset", "scanjob",
            "application", "appenvconfig", "appdependency",
        ]

        def perms(*actions):
            result = []
            for action in actions:
                for model in app_models:
                    try:
                        ct = ContentType.objects.get(app_label="asset_manager", model=model)
                        p = Permission.objects.get(content_type=ct, codename=f"{action}_{model}")
                        result.append(p)
                    except (ContentType.DoesNotExist, Permission.DoesNotExist):
                        pass
            return result

        g_owner,  _ = Group.objects.get_or_create(name="Owner")
        g_owner.permissions.set(perms("view", "add", "change", "delete"))

        g_infra,  _ = Group.objects.get_or_create(name="Infra Admin")
        g_infra.permissions.set(perms("view", "add", "change"))

        g_app,    _ = Group.objects.get_or_create(name="App Admin")
        app_only = []
        for action in ("view", "add", "change"):
            for model in ("application", "appenvconfig", "appdependency"):
                try:
                    ct = ContentType.objects.get(app_label="asset_manager", model=model)
                    app_only.append(Permission.objects.get(content_type=ct, codename=f"{action}_{model}"))
                except (ContentType.DoesNotExist, Permission.DoesNotExist):
                    pass
        app_only += perms("view")
        g_app.permissions.set(app_only)

        g_viewer, _ = Group.objects.get_or_create(name="Viewer")
        g_viewer.permissions.set(perms("view"))

        self.stdout.write("  groups OK")

        def make_user(username, email, full_name, password, group, is_staff=False):
            first, *rest = full_name.split()
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "email": email, "first_name": first, "last_name": "".join(rest),
                    "is_staff": is_staff, "is_active": True,
                },
            )
            if created:
                user.set_password(password)
                user.save()
            user.groups.add(group)
            return user

        u_tabata  = make_user("tabata_hiroshi", "h.tabata@s-arcana.co.jp", "Hiroshi Tabata", "Passw0rd!", g_owner,  is_staff=True)
        u_tanaka  = make_user("tanaka_kenji",   "k.tanaka@s-arcana.co.jp", "Kenji Tanaka",   "Passw0rd!", g_infra,  is_staff=True)
        u_yamada  = make_user("yamada_yuki",    "y.yamada@s-arcana.co.jp", "Yuki Yamada",    "Passw0rd!", g_app)
        u_sato    = make_user("sato_mai",       "m.sato@s-arcana.co.jp",   "Mai Sato",       "Passw0rd!", g_viewer)
        u_suzuki  = make_user("suzuki_taro",    "t.suzuki@demo-corp.com",  "Taro Suzuki",    "Passw0rd!", g_owner,  is_staff=True)
        u_dviewer = make_user("demo_viewer",    "viewer@demo-corp.com",    "Demo Viewer","Passw0rd!", g_viewer)

        self.stdout.write("  users OK")

        for user, org, role in [
            (u_tabata,  org_a, Membership.Role.OWNER),
            (u_tanaka,  org_a, Membership.Role.INFRA_ADMIN),
            (u_yamada,  org_a, Membership.Role.APP_ADMIN),
            (u_sato,    org_a, Membership.Role.VIEWER),
            (u_suzuki,  org_b, Membership.Role.OWNER),
            (u_dviewer, org_b, Membership.Role.VIEWER),
            (u_sato,    org_b, Membership.Role.VIEWER),
        ]:
            Membership.objects.get_or_create(user=user, organization=org, defaults={"role": role})

        # Superusers (admin / root, etc.) are intentionally NOT assigned an organization.
        # They are management-only (Django admin); the org-scoped app is used by
        # organization members with roles (tabata_hiroshi, etc.).
        # OrgRequiredMiddleware redirects users without an organization to /admin/.

        self.stdout.write("  memberships OK")
