"""
認可（マルチテナント分離 / IDOR）の回帰テスト。
org A のユーザーが org B のオブジェクトに ID 指定でアクセスできないことを保証する。
"""

import pytest
from django.test import Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils import timezone

from asset_manager.models import (
    Organization, System, Environment, Asset, Membership,
)

User = get_user_model()
pytestmark = pytest.mark.django_db


def _make_org(name, code):
    org = Organization.objects.create(name=name, slug=code)
    system = System.objects.create(name=f'{name}-sys', code=code, organization=org)
    env = Environment.objects.create(system=system, name='prod', env_type='PROD')
    asset = Asset.objects.create(
        environment=env, name='web', provider='AWS', asset_type='EC2',
        asset_category='COMPUTE', cloud_id=f'i-{code}', region='ap-northeast-1',
        raw_data={'id': f'i-{code}'}, last_imported_at=timezone.now(),
    )
    return org, system, env, asset


def _client_for_org(org, username):
    user = User.objects.create_user(username=username, password='pw')
    Membership.objects.create(user=user, organization=org, role=Membership.Role.OWNER)
    c = Client()
    c.force_login(user)
    return c


class TestCrossOrgIsolation:
    """org A の認証済みユーザーは org B の資源に触れない（全て 404）。"""

    def test_cannot_read_or_mutate_other_orgs_objects(self):
        org_a, _, _, _ = _make_org('orgA', 'a')
        _, sys_b, env_b, asset_b = _make_org('orgB', 'b')
        c = _client_for_org(org_a, 'user-a')

        # 読み取り系（GET）→ 404
        assert c.get(reverse('asset-detail', args=[asset_b.id])).status_code == 404
        assert c.get(reverse('env-drift', args=[env_b.id])).status_code == 404
        assert c.get(reverse('environment-list', args=[sys_b.id])).status_code == 404

        # 破壊・実行系（POST）→ 404、かつ実際に消えていない
        assert c.post(reverse('env-scan', args=[env_b.id])).status_code == 404
        assert c.post(reverse('system-delete', args=[sys_b.id])).status_code == 404
        assert c.post(reverse('asset-delete', args=[asset_b.id])).status_code == 404
        assert System.objects.filter(pk=sys_b.id).exists()
        assert Asset.objects.filter(pk=asset_b.id).exists()

    def test_can_access_own_orgs_objects(self):
        """スコープを入れても自組織のオブジェクトには正常アクセスできる（過剰ブロックでない）。"""
        org_a, _, _, asset_a = _make_org('orgA', 'a')
        c = _client_for_org(org_a, 'user-a')
        assert c.get(reverse('asset-detail', args=[asset_a.id])).status_code == 200

    def test_user_without_membership_sees_nothing(self):
        """メンバーシップ無しユーザーは自組織が無い＝単一オブジェクトは 404。"""
        _, _, _, asset_b = _make_org('orgB', 'b')
        user = User.objects.create_user(username='nobody', password='pw')
        c = Client(); c.force_login(user)
        assert c.get(reverse('asset-detail', args=[asset_b.id])).status_code == 404


class TestAuditLogAuth:
    def test_audit_log_requires_login(self):
        """未認証では監査ログを取得できない（以前はデコレータ漏れで全組織露出）。"""
        c = Client()
        resp = c.get(reverse('audit-log'))
        assert resp.status_code != 200  # ログインへリダイレクト等
