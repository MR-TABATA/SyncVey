"""
OpenTofu の state を取り込めるか。

OpenTofu は Terraform のフォークで、state の形式（version 4）は同じ。違いは
  - provider が registry.opentofu.org/... になる
  - terraform_version に OpenTofu 自身のバージョンが入る
  - 1.7 以降は state 暗号化が使える。暗号化されたファイルは resources を持たず、
    {"serial", "lineage", "meta", "encrypted_data"} だけになる（復号しないと読めない）
実機で作った state ではなく、公開されている形式に合わせたもの。
"""
import json

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from asset_manager.models import Asset, Membership, Organization

OPENTOFU_STATE = {
    "version": 4,
    "terraform_version": "1.8.2",
    "serial": 3,
    "lineage": "0b7d8e0a-aaaa-bbbb-cccc-000000000001",
    "outputs": {
        "system": {"value": "tofu-sys", "type": "string"},
        "env":    {"value": "prod",     "type": "string"},
    },
    "resources": [
        {
            "mode": "managed", "type": "aws_db_instance", "name": "db",
            "provider": 'provider["registry.opentofu.org/hashicorp/aws"]',
            "instances": [{"schema_version": 2, "attributes": {
                "id": "tofu-db", "engine": "postgres", "engine_version": "16.2",
                "instance_class": "db.t3.micro",
            }}],
        },
        {
            "mode": "managed", "type": "aws_lambda_function", "name": "fn",
            "provider": 'provider["registry.opentofu.org/hashicorp/aws"]',
            "instances": [{"schema_version": 0, "attributes": {
                "id": "tofu-fn", "function_name": "tofu-fn", "runtime": "python3.12",
                "arn": "arn:aws:lambda:ap-northeast-1:111111111111:function:tofu-fn",
            }}],
        },
    ],
}

ENCRYPTED_STATE = {
    "serial": 3,
    "lineage": "0b7d8e0a-aaaa-bbbb-cccc-000000000001",
    "meta": {"key_provider.pbkdf2.main": "eyJzYWx0IjoiMTIzIn0="},
    "encrypted_data": "AAAA-not-real-ciphertext",
    "encryption_version": "v0",
}


def _client():
    org = Organization.objects.create(name='o', slug='o')
    user = get_user_model().objects.create_user(username='u', password='pw')
    Membership.objects.create(user=user, organization=org, role=Membership.Role.OWNER)
    c = Client()
    c.force_login(user)
    return c


def _upload(c, state, name='infra-prod.tfstate'):
    body = json.dumps(state).encode('utf-8')
    return c.post(reverse('upload-tfstate'), {'tfstate_file': SimpleUploadedFile(name, body)})


@pytest.mark.django_db
def test_opentofu_state_is_imported_like_terraform():
    resp = _upload(_client(), OPENTOFU_STATE)
    assert resp.status_code == 200
    assert Asset.objects.count() == 2
    db = Asset.objects.get(cloud_id='tofu-db')
    assert (db.asset_type, db.raw_data['engine'], db.raw_data['engine_version']) == ('RDS', 'postgres', '16.2')
    assert Asset.objects.get(cloud_id='tofu-fn').asset_type == 'LAMBDA'


@pytest.mark.django_db
def test_encrypted_opentofu_state_is_refused_with_a_clear_reason():
    resp = _upload(_client(), ENCRYPTED_STATE)
    html = resp.content.decode()
    assert Asset.objects.count() == 0
    assert 'state file is encrypted' in html
    assert 'tofu state pull' in html
    # 以前は「0件登録」で終わり、取り込めたように見えていた
    assert 'asset(s) registered' not in html
