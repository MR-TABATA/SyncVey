"""
EOL データ判定・外部取得のユニットテスト。
endoflife.date への HTTP は mock し、実通信はしない。
"""
import json
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
