"""
共通フィクスチャ。
unit test / E2E test 両方から使われる。
"""

import json
import pytest
from django.contrib.auth import get_user_model

from asset_manager.models import Organization, System, Environment, Membership

User = get_user_model()


# ---------------------------------------------------------------------------
# DB fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def org(db):
    return Organization.objects.create(name='テスト組織')


@pytest.fixture
def system(db, org):
    return System.objects.create(
        name='テストシステム',
        code='test-system',
        organization=org,
        aws_scan_regions=['ap-northeast-1'],
    )


@pytest.fixture
def environment(db, system):
    return Environment.objects.create(
        system=system,
        name='prod',
        env_type='PROD',
    )


@pytest.fixture
def asset_in_env(db, environment):
    """ダッシュボードにシステムが表示されるよう、資産を1件作成する。"""
    from asset_manager.models import Asset
    from django.utils import timezone
    return Asset.objects.create(
        environment=environment,
        name='web-server',
        provider='AWS',
        asset_type='EC2',
        asset_category='COMPUTE',
        cloud_id='i-fixture001',
        region='ap-northeast-1',
        raw_data={'id': 'i-fixture001', 'instance_type': 't3.micro'},
        last_imported_at=timezone.now(),
    )


@pytest.fixture
def admin_user(db, org):
    user = User.objects.create_superuser(
        username='admin',
        password='testpass123',
    )
    # システム等のフィクスチャと同じ org に所属させる（認可スコープを満たすため）
    Membership.objects.create(user=user, organization=org, role=Membership.Role.OWNER)
    return user


# ---------------------------------------------------------------------------
# Playwright fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def logged_in_page(page, live_server, admin_user):
    """ログイン済みのブラウザページを返す。"""
    page.goto(f"{live_server.url}/login/")
    page.fill("[name=username]", "admin")
    page.fill("[name=password]", "testpass123")
    page.click("[type=submit]")
    page.wait_for_url(f"{live_server.url}/")
    # wait_for_url は URL が一致した時点で返るため、直後に goto/evaluate を
    # 重ねると遷移が確定しておらず実行コンテキストが壊れることがある。
    page.wait_for_load_state("load")
    return page


# ---------------------------------------------------------------------------
# サンプル tfstate（アップロードテスト用）
# ---------------------------------------------------------------------------

MINIMAL_TFSTATE = {
    "version": 4,
    "terraform_version": "1.5.0",
    "serial": 1,
    "lineage": "e2e-test-lineage",
    "outputs": {
        "system": {"value": "e2eシステム", "type": "string"},
        "env":    {"value": "prod",        "type": "string"},
    },
    "resources": [
        {
            "mode": "managed",
            "type": "aws_instance",
            "name": "web",
            "provider": 'provider["registry.terraform.io/hashicorp/aws"]',
            "instances": [{
                "schema_version": 1,
                "attributes": {
                    "id":            "i-e2etest001",
                    "instance_type": "t3.micro",
                    "ami":           "ami-12345678",
                },
            }],
        },
        {
            "mode": "managed",
            "type": "aws_db_instance",
            "name": "db",
            "provider": 'provider["registry.terraform.io/hashicorp/aws"]',
            "instances": [{
                "schema_version": 1,
                "attributes": {
                    "id":             "prod-db",
                    "engine":         "mysql",
                    "engine_version": "8.0.35",
                    "instance_class": "db.t3.micro",
                },
            }],
        },
    ],
}


@pytest.fixture
def tfstate_file(tmp_path):
    """一時ディレクトリに .tfstate ファイルを作成して返す。"""
    path = tmp_path / "infra.tfstate"
    path.write_text(json.dumps(MINIMAL_TFSTATE), encoding="utf-8")
    return path


# tfstate with secrets — 警告フローのテスト用
TFSTATE_WITH_SECRETS = {
    "version": 4,
    "terraform_version": "1.5.0",
    "serial": 1,
    "lineage": "e2e-secrets-lineage",
    "outputs": {
        "system": {"value": "secretsテスト", "type": "string"},
        "env":    {"value": "prod",          "type": "string"},
    },
    "resources": [
        {
            "mode": "managed",
            "type": "aws_db_instance",
            "name": "db",
            "provider": 'provider["registry.terraform.io/hashicorp/aws"]',
            "instances": [{
                "schema_version": 1,
                "attributes": {
                    "id":       "secrets-db",
                    "engine":   "mysql",
                    "password": "SuperSecret123!",
                },
            }],
        },
    ],
}


@pytest.fixture
def tfstate_file_with_secrets(tmp_path):
    """機密情報を含む .tfstate ファイルを作成して返す。"""
    path = tmp_path / "secrets.tfstate"
    path.write_text(json.dumps(TFSTATE_WITH_SECRETS), encoding="utf-8")
    return path
