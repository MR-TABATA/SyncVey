"""
EOL データ判定・外部取得のユニットテスト。
endoflife.date への HTTP は mock し、実通信はしない。
"""
import json

import pytest
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from asset_manager import eol_data, eol_refresh
from asset_manager.eol_data import get_eol_status, invalidate_cache
from asset_manager.eol_refresh import refresh_eol, _fetch_product, _normalize_eol
from asset_manager.models import EolSnapshot


class TestStaticFallback(TestCase):
    """スナップショットが無いとき、内蔵辞書 _EOL で判定する。"""

    def setUp(self):
        invalidate_cache()

    def tearDown(self):
        invalidate_cache()

    def test_known_eol(self):
        self.assertEqual(get_eol_status('nginx', '1.20'), 'eol')

    def test_supported_is_ok(self):
        self.assertEqual(get_eol_status('Python', '3.13'), 'ok')

    def test_alias_resolved(self):
        # PHP-FPM -> php
        self.assertEqual(get_eol_status('PHP-FPM', '8.0'), 'eol')

    def test_no_eol_concept_is_unknown(self):
        self.assertEqual(get_eol_status('gunicorn', '21.2'), 'unknown')

    def test_unknown_product(self):
        self.assertEqual(get_eol_status('totally-unknown', '1.0'), 'unknown')


class TestNormalizeEol(TestCase):
    def test_mapping(self):
        self.assertEqual(_normalize_eol('2025-12-31'), '2025-12-31')
        self.assertEqual(_normalize_eol('2025-12-31T00:00:00'), '2025-12-31')
        self.assertEqual(_normalize_eol(True), '1970-01-01')   # EOL だが日付不明
        self.assertIsNone(_normalize_eol(False))               # サポート中
        self.assertIsNone(_normalize_eol(None))


class TestFetchProductParsing(TestCase):
    def test_parses_cycle_list(self):
        payload = [
            {"cycle": "1.20", "eol": "2022-04-01"},
            {"cycle": "1.21", "eol": False},
            {"cycle": "1.19", "eol": True},
        ]
        resp = MagicMock()
        resp.status = 200
        resp.read.return_value = json.dumps(payload).encode()
        cm = MagicMock()
        cm.__enter__.return_value = resp
        with patch('urllib.request.urlopen', return_value=cm):
            out = _fetch_product('nginx')
        self.assertEqual(out, {'1.20': '2022-04-01', '1.21': None, '1.19': '1970-01-01'})

    def test_404_returns_none(self):
        import urllib.error
        with patch('urllib.request.urlopen', side_effect=urllib.error.HTTPError(
                'u', 404, 'nf', {}, None)):
            self.assertIsNone(_fetch_product('does-not-exist'))


class TestRefreshOptIn(TestCase):
    def setUp(self):
        invalidate_cache()

    def tearDown(self):
        invalidate_cache()

    @override_settings(EOL_REFRESH_ENABLED=False)
    def test_disabled_skips_without_force(self):
        result = refresh_eol()
        self.assertTrue(result['skipped'])
        self.assertEqual(EolSnapshot.objects.count(), 0)

    @override_settings(EOL_REFRESH_ENABLED=False)
    def test_force_overrides_and_snapshot_is_used(self):
        # 取得はモック: nginx だけ過去日の cycle を返す
        def fake_fetch(slug):
            return {'9.9': '2000-01-01'} if slug == 'nginx' else None

        with patch('asset_manager.eol_refresh._fetch_product', side_effect=fake_fetch):
            result = refresh_eol(force=True)

        self.assertTrue(result['ok'])
        self.assertEqual(EolSnapshot.objects.count(), 1)

        invalidate_cache()
        # スナップショットの cycle が判定に反映される
        self.assertEqual(get_eol_status('nginx', '9.9'), 'eol')


class TestAssetEol(TestCase):
    """資産（RDS エンジン / Lambda ランタイム / EKS バージョン）の期限判定。"""

    def setUp(self):
        invalidate_cache()

    def tearDown(self):
        invalidate_cache()

    def test_rds_engine_and_version_are_judged(self):
        from asset_manager.eol_data import get_asset_eol_status, asset_eol_target
        self.assertEqual(asset_eol_target('RDS', {'engine': 'postgres', 'engine_version': '12.17'}), ('postgresql', '12.17'))
        self.assertEqual(get_asset_eol_status('RDS', {'engine': 'postgres', 'engine_version': '9.6.24'}), 'eol')
        self.assertEqual(get_asset_eol_status('RDS', {'engine': 'mysql', 'engine_version': '5.7.44'}), 'eol')

    def test_aurora_uses_the_leading_version(self):
        from asset_manager.eol_data import asset_eol_target
        self.assertEqual(asset_eol_target('RDS', {'engine': 'aurora-mysql', 'engine_version': '5.7.mysql_aurora.2.11.2'}), ('mysql', '5.7'))
        self.assertEqual(asset_eol_target('RDS', {'engine': 'aurora-postgresql', 'engine_version': '15.4'}), ('postgresql', '15.4'))

    def test_unsupported_or_missing_is_unknown_not_an_error(self):
        from asset_manager.eol_data import get_asset_eol_status
        self.assertEqual(get_asset_eol_status('RDS', {'engine': 'oracle-ee', 'engine_version': '19'}), 'unknown')
        self.assertEqual(get_asset_eol_status('RDS', {'engine': 'postgres'}), 'unknown')
        self.assertEqual(get_asset_eol_status('RDS', {}), 'unknown')
        self.assertEqual(get_asset_eol_status('RDS', None), 'unknown')
        self.assertEqual(get_asset_eol_status('EC2', {'engine': 'postgres', 'engine_version': '9.6'}), 'unknown')

    def test_lambda_runtime_names(self):
        from asset_manager.eol_data import asset_eol_target
        t = lambda rt: asset_eol_target('LAMBDA', {'runtime': rt})
        self.assertEqual(t('python3.9'), ('python', '3.9'))
        self.assertEqual(t('nodejs18.x'), ('nodejs', '18'))
        self.assertEqual(t('ruby3.2'), ('ruby', '3.2'))
        self.assertEqual(t('java11'), ('java', '11'))
        self.assertEqual(t('java8.al2'), ('java', '8'))
        # 対象外: dotnet / go1.x / provided / 空（コンテナイメージの Lambda）
        for rt in ('dotnet8', 'go1.x', 'provided.al2023', ''):
            self.assertIsNone(t(rt), rt)

    def test_lambda_judged_against_the_language_eol(self):
        from asset_manager.eol_data import get_asset_eol_status
        self.assertEqual(get_asset_eol_status('LAMBDA', {'runtime': 'python3.7'}), 'eol')
        self.assertEqual(get_asset_eol_status('LAMBDA', {'runtime': 'python3.13'}), 'ok')

    def test_eks_is_judged_only_from_the_snapshot(self):
        from asset_manager.eol_data import get_asset_eol_status
        # スナップショットが無ければ、日付を持たないので unknown（嘘の判定をしない）
        self.assertEqual(get_asset_eol_status('EKS', {'version': '1.24'}), 'unknown')
        EolSnapshot.objects.create(data={'amazon-eks': {'1.24': '2025-01-31', '1.99': '2999-01-01'}})
        invalidate_cache()
        self.assertEqual(get_asset_eol_status('EKS', {'version': '1.24'}), 'eol')
        self.assertEqual(get_asset_eol_status('EKS', {'version': '1.99'}), 'ok')
        self.assertEqual(get_asset_eol_status('EKS', {}), 'unknown')

    def test_eks_floor_is_the_oldest_version_still_in_standard_support(self):
        from asset_manager.eol_data import eks_supported_floor
        self.assertIsNone(eks_supported_floor())          # データが無ければ言わない
        EolSnapshot.objects.create(data={'amazon-eks': {
            '1.24': '2025-01-31', '1.30': '2024-07-23', '1.9': '2099-01-01',
            '1.31': '2099-06-01', '1.32': None, '1.100': '2100-01-01'}})
        invalidate_cache()
        # 文字列順なら '1.100' < '1.31' になる。数として比べる。期限切れ（1.24, 1.30）は除く
        self.assertEqual(eks_supported_floor(), '1.9')
        EolSnapshot.objects.create(data={'amazon-eks': {'1.9': '2000-01-01'}})
        invalidate_cache()

    def test_eks_is_a_fetch_target(self):
        from asset_manager.eol_data import base_products
        self.assertIn('amazon-eks', base_products())


@pytest.mark.django_db
class TestAssetListShowsEngineAndEol:
    """一覧画面: RDS はインスタンスクラスの下にエンジンも出す（以前は instance_class に隠れて出なかった）。
    期限切れのエンジンにはバッジが付く。"""

    def _list(self, raw):
        from django.test import Client
        from django.urls import reverse
        from django.contrib.auth import get_user_model
        from asset_manager.models import Asset, Environment, Membership, Organization, System
        org = Organization.objects.create(name='o', slug='o')
        system = System.objects.create(name='s', code='s', organization=org)
        env = Environment.objects.create(system=system, name='prod', env_type='PROD')
        Asset.objects.create(environment=env, name='db1', provider='AWS', asset_type='RDS',
                             asset_category='DATABASE', cloud_id='db1', region='ap-northeast-1', raw_data=raw)
        user = get_user_model().objects.create_user(username='u', password='pw')
        Membership.objects.create(user=user, organization=org, role=Membership.Role.OWNER)
        c = Client()
        c.force_login(user)
        return c.get(reverse('asset-list')).content.decode()

    def test_engine_is_shown_next_to_instance_class(self):
        html = self._list({'instance_class': 'db.t3.micro', 'engine': 'postgres', 'engine_version': '16.2'})
        assert 'db.t3.micro' in html
        assert 'postgres 16.2' in html
        assert 'past end-of-life' not in html

    def test_an_old_engine_gets_the_badge(self):
        html = self._list({'instance_class': 'db.t3.micro', 'engine': 'postgres', 'engine_version': '9.6.24'})
        assert 'postgres 9.6.24' in html
        # バッジは「RDS」というサービスの隣でなく、期限が切れたエンジンの行（アイコンつき）に付く
        assert 'data-lucide="database"' in html
        assert 'past end-of-life' in html
        # エンジンの行は、表とカードの 2 つの並びに 1 回ずつ（生のエンジン行と二重に出さない）
        assert html.count('postgres 9.6.24') == 2

    def test_lambda_and_eks_name_the_component_line(self):
        from django.test import Client
        from django.urls import reverse
        from django.contrib.auth import get_user_model
        from asset_manager.models import Asset, Environment, Membership, Organization, System
        org = Organization.objects.create(name='o', slug='o')
        system = System.objects.create(name='s', code='s', organization=org)
        env = Environment.objects.create(system=system, name='prod', env_type='PROD')
        for cid, t, raw in (('fn', 'LAMBDA', {'runtime': 'python3.7'}), ('fn-ok', 'LAMBDA', {'runtime': 'python3.13'})):
            Asset.objects.create(environment=env, name=cid, provider='AWS', asset_type=t,
                                 asset_category='COMPUTE', cloud_id=cid, raw_data=raw)
        user = get_user_model().objects.create_user(username='u', password='pw')
        Membership.objects.create(user=user, organization=org, role=Membership.Role.OWNER)
        c = Client()
        c.force_login(user)
        html = c.get(reverse('asset-list')).content.decode()
        assert 'data-lucide="terminal"' in html
        assert 'python3.7' in html and 'python3.13' in html
        assert html.count('past end-of-life') == 2      # python3.7 の 1 件を、表とカードに 1 回ずつ


@pytest.mark.django_db
class TestAssetDetailShowsAttributesAndEol:
    """詳細の窓: 保存済みの属性と、EOL の判定（期限つき）を出す。以前は属性が何も出なかった。"""

    def _detail(self, asset_type, raw):
        from django.test import Client
        from django.urls import reverse
        from django.contrib.auth import get_user_model
        from asset_manager.models import Asset, Environment, Membership, Organization, System
        org = Organization.objects.create(name='o', slug='o')
        system = System.objects.create(name='s', code='s', organization=org)
        env = Environment.objects.create(system=system, name='prod', env_type='PROD')
        a = Asset.objects.create(environment=env, name='x', provider='AWS', asset_type=asset_type,
                                 asset_category='DATABASE', cloud_id='x', region='ap-northeast-1', raw_data=raw)
        user = get_user_model().objects.create_user(username='u', password='pw')
        Membership.objects.create(user=user, organization=org, role=Membership.Role.OWNER)
        c = Client()
        c.force_login(user)
        return c.get(reverse('asset-detail', args=[a.id])).content.decode()

    def test_attributes_are_listed_without_internal_keys_or_empties(self):
        html = self._detail('RDS', {'engine': 'postgres', 'engine_version': '9.6.24', 'multi_az': False,
                                    'tags': {'Env': 'prod'}, 'empty': '', '_resource_type': 'aws_db_instance'})
        assert 'engine_version' in html and '9.6.24' in html
        assert 'multi_az' in html and 'false' in html
        assert '{&quot;Env&quot;: &quot;prod&quot;}' in html
        assert '_resource_type' not in html
        assert '>empty<' not in html

    def test_an_old_engine_shows_the_eol_panel_with_its_date(self):
        html = self._detail('RDS', {'engine': 'postgres', 'engine_version': '9.6.24'})
        assert 'past end-of-life' in html
        assert 'PostgreSQL 9.6.24' in html
        assert '2021-11-11' in html       # PostgreSQL 9.6 の終了日（内蔵データ）

    def test_the_panel_names_the_middleware_not_the_service(self):
        html = self._detail('LAMBDA', {'runtime': 'python3.13', 'function_name': 'f'})
        assert 'Middleware support' in html
        assert 'Python 3.13' in html and 'Runtime' in html
        assert 'Supported' in html
        assert 'past end-of-life' not in html

    def test_header_has_a_working_icon_and_readable_pills(self):
        """以前は、実在しないフォルダを指す壊れたアイコンと、文字のない空の丸が出ていた。"""
        import os
        from django.conf import settings
        html = self._detail('RDS', {'engine': 'postgres', 'engine_version': '16.2'})
        import re
        src = re.search(r'<img src="([^"]+)"[^>]*alt="RDS"', html).group(1)
        assert 'cloud-icons/aws/rds.svg' in src
        rel = src.split('/static/', 1)[1]
        assert any(os.path.exists(os.path.join(d, rel)) for d in settings.STATICFILES_DIRS), rel
        # 提供元と種別の丸に、文字が入っている
        assert re.search(r'>\s*AWS\s*</span>', html)
        assert re.search(r'>\s*RDS\s*</span>', html)

    def test_no_eol_panel_for_things_we_cannot_judge(self):
        html = self._detail('EC2', {'instance_type': 't3.micro'})
        assert 'end-of-life' not in html
        assert 'instance_type' in html


@pytest.mark.django_db
def test_eks_card_says_where_to_go(settings):
    """EKS の期限切れには、「Kubernetes 1.24 はサポート終了」に続けて「EKS は 1.31 以上をサポート」を出す。"""
    from django.test import Client
    from django.urls import reverse
    from django.contrib.auth import get_user_model
    from asset_manager.models import Asset, EolSnapshot, Environment, Membership, Organization, System
    from asset_manager.eol_data import invalidate_cache
    EolSnapshot.objects.create(data={'amazon-eks': {'1.24': '2025-01-31', '1.31': '2099-01-01'}})
    invalidate_cache()
    org = Organization.objects.create(name='o', slug='o')
    system = System.objects.create(name='s', code='s', organization=org)
    env = Environment.objects.create(system=system, name='prod', env_type='PROD')
    Asset.objects.create(environment=env, name='c', provider='AWS', asset_type='EKS',
                         asset_category='COMPUTE', cloud_id='c', raw_data={'version': '1.24'})
    user = get_user_model().objects.create_user(username='u', password='pw')
    Membership.objects.create(user=user, organization=org, role=Membership.Role.OWNER)
    c = Client()
    c.force_login(user)
    html = c.get(reverse('asset-list')).content.decode()
    assert 'Kubernetes 1.24' in html and 'past end-of-life' in html
    assert 'EKS supports Kubernetes 1.31 and later' in html
    invalidate_cache()
