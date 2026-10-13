"""ライブラリの脆弱性: OSV 取得（通信はモック）・ロックファイル取り込み・画面・権限。"""
import json
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from asset_manager import vulns
from asset_manager.dependency_import import import_lockfile
from asset_manager.models import (AppDependency, Application, AppEnvConfig, Environment, Membership,
                                  Organization, System, VulnRecord)

pytestmark = pytest.mark.django_db

DJANGO_VULN = {
    'id': 'GHSA-aaaa', 'aliases': ['CVE-2022-28346'], 'summary': 'SQL Injection in Django',
    'database_specific': {'severity': 'CRITICAL'},
    'affected': [{'package': {'name': 'django', 'ecosystem': 'PyPI'},
                  'ranges': [{'type': 'ECOSYSTEM', 'events': [{'introduced': '2.2'}, {'fixed': '2.2.28'}]}]},
                 {'package': {'name': 'other', 'ecosystem': 'PyPI'},
                  'ranges': [{'type': 'ECOSYSTEM', 'events': [{'fixed': '9.9'}]}]}],
}
MILD_VULN = {'id': 'GHSA-bbbb', 'aliases': [], 'summary': 'minor', 'database_specific': {'severity': 'MODERATE'}, 'affected': []}


def _make_cfg(org_name='o'):
    org = Organization.objects.create(name=org_name, slug=org_name)
    system = System.objects.create(name=f's-{org_name}', code=f's-{org_name}', organization=org)
    env = Environment.objects.create(system=system, name='prod', env_type='PROD')
    app = Application.objects.create(system=system, name='app', language='python')
    return org, AppEnvConfig.objects.create(application=app, environment=env)


def _client(org, username='u'):
    user = get_user_model().objects.create_user(username=username, password='pw')
    Membership.objects.create(user=user, organization=org, role=Membership.Role.OWNER)
    c = Client()
    c.force_login(user)
    return c


def _fake_osv(by_name):
    """OSV のモック: by_name = {'django': [vuln, ...]}。"""
    def request(url, payload=None):
        if url == vulns.KEV_URL:
            return {'vulnerabilities': [{'cveID': 'CVE-2022-28346'}]}
        if url == vulns.OSV_BATCH:
            return {'results': [{'vulns': [{'id': v['id']} for v in by_name.get(q['package']['name'], [])]}
                                for q in payload['queries']]}
        if url == vulns.OSV_QUERY:
            return {'vulns': by_name.get(payload['package']['name'], [])}
        raise AssertionError(url)
    return request


class TestImport:
    def test_replaces_the_same_ecosystem_and_keeps_manual_rows(self):
        _, cfg = _make_cfg()
        AppDependency.objects.create(app_env_config=cfg, name='nginx', version='1.24', dep_type='middleware')
        import_lockfile(cfg, 'requirements.txt', b'django==3.2.0\nold==1.0\n')
        r = import_lockfile(cfg, 'requirements.txt', b'django==3.2.0\nflask==2.0.1\n')
        assert (r['added'], r['removed'], r['total']) == (1, 1, 2)
        names = set(cfg.dependencies.values_list('name', flat=True))
        assert names == {'nginx', 'django', 'flask'}
        assert cfg.dependencies.get(name='django').ecosystem == 'PyPI'

    def test_same_package_in_two_versions_is_kept(self):
        _, cfg = _make_cfg()
        lock = {'lockfileVersion': 3, 'packages': {'node_modules/lodash': {'version': '4.17.15'},
                                                   'node_modules/a/node_modules/lodash': {'version': '3.0.0'}}}
        import_lockfile(cfg, 'package-lock.json', json.dumps(lock).encode())
        assert sorted(cfg.dependencies.values_list('version', flat=True)) == ['3.0.0', '4.17.15']


class TestRefresh:
    def test_disabled_by_default_makes_no_request(self, settings):
        settings.VULN_REFRESH_ENABLED = False
        with mock.patch('asset_manager.vulns._request') as req:
            assert vulns.refresh_vulns()['skipped'] is True
        req.assert_not_called()

    def test_stores_advisories_severity_fixed_and_kev(self):
        _, cfg = _make_cfg()
        import_lockfile(cfg, 'requirements.txt', b'django==3.2.0\nsafe==1.0\n')
        with mock.patch('asset_manager.vulns._request', side_effect=_fake_osv({'django': [DJANGO_VULN, MILD_VULN]})):
            result = vulns.refresh_vulns(force=True)
        assert (result['ok'], result['packages'], result['vulnerable']) == (True, 2, 1)
        rec = VulnRecord.objects.get(name='django')
        assert rec.max_severity == 'CRITICAL' and rec.has_kev is True
        first, second = rec.advisories
        assert first['id'] == 'GHSA-aaaa' and first['kev'] and first['fixed'] == ['2.2.28']   # 別パッケージの修正版は混ぜない
        assert second['severity'] == 'MEDIUM' and not second['kev']                          # MODERATE -> MEDIUM
        assert first['url'] == 'https://osv.dev/vulnerability/GHSA-aaaa'
        assert VulnRecord.objects.get(name='safe').advisories == []                          # 「確認済みで問題なし」を残す

    def test_only_sends_package_name_ecosystem_and_version(self):
        org, cfg = _make_cfg('secret-org')
        import_lockfile(cfg, 'requirements.txt', b'django==3.2.0\n')
        sent = []
        base = _fake_osv({})
        def spy(url, payload=None):
            sent.append(json.dumps(payload))
            return base(url, payload)
        with mock.patch('asset_manager.vulns._request', side_effect=spy):
            vulns.refresh_vulns(force=True)
        assert all('secret-org' not in s and 'app' not in s.replace('django', '') for s in sent)
        assert json.loads(sent[0]) == {'queries': [{'package': {'name': 'django', 'ecosystem': 'PyPI'}, 'version': '3.2.0'}]}

    def test_manual_rows_without_ecosystem_are_not_queried(self):
        _, cfg = _make_cfg()
        AppDependency.objects.create(app_env_config=cfg, name='nginx', version='1.24', dep_type='middleware')
        with mock.patch('asset_manager.vulns._request') as req:
            r = vulns.refresh_vulns(force=True)
        req.assert_not_called()
        assert r['packages'] == 0

    def test_osv_down_keeps_the_previous_result(self):
        import urllib.error
        _, cfg = _make_cfg()
        import_lockfile(cfg, 'requirements.txt', b'django==3.2.0\n')
        VulnRecord.objects.create(ecosystem='PyPI', name='django', version='3.2.0', advisories=[{'id': 'X'}], max_severity='HIGH')
        with mock.patch('asset_manager.vulns._request', side_effect=urllib.error.URLError('down')):
            r = vulns.refresh_vulns(force=True)
        assert r['ok'] is False
        assert VulnRecord.objects.get(name='django').advisories == [{'id': 'X'}]

    def test_detail_failure_does_not_overwrite_with_clean(self):
        import urllib.error
        _, cfg = _make_cfg()
        import_lockfile(cfg, 'requirements.txt', b'django==3.2.0\n')
        VulnRecord.objects.create(ecosystem='PyPI', name='django', version='3.2.0', advisories=[{'id': 'X'}], max_severity='HIGH')
        base = _fake_osv({'django': [DJANGO_VULN]})
        def flaky(url, payload=None):
            if url == vulns.OSV_QUERY:
                raise urllib.error.URLError('boom')
            return base(url, payload)
        with mock.patch('asset_manager.vulns._request', side_effect=flaky):
            r = vulns.refresh_vulns(force=True)
        assert r['failed'] == 1
        assert VulnRecord.objects.get(name='django').advisories == [{'id': 'X'}]

    def test_kev_failure_still_stores_vulnerabilities(self):
        import urllib.error
        _, cfg = _make_cfg()
        import_lockfile(cfg, 'requirements.txt', b'django==3.2.0\n')
        base = _fake_osv({'django': [DJANGO_VULN]})
        def no_kev(url, payload=None):
            if url == vulns.KEV_URL:
                raise urllib.error.URLError('down')
            return base(url, payload)
        with mock.patch('asset_manager.vulns._request', side_effect=no_kev):
            vulns.refresh_vulns(force=True)
        rec = VulnRecord.objects.get(name='django')
        assert rec.advisories and rec.has_kev is False

    def test_records_for_removed_libraries_are_dropped(self):
        _, cfg = _make_cfg()
        import_lockfile(cfg, 'requirements.txt', b'django==3.2.0\n')
        VulnRecord.objects.create(ecosystem='PyPI', name='gone', version='1.0')
        with mock.patch('asset_manager.vulns._request', side_effect=_fake_osv({})):
            vulns.refresh_vulns(force=True)
        assert not VulnRecord.objects.filter(name='gone').exists()


class TestScreen:
    def _upload(self, c, cfg, name, content):
        return c.post(reverse('dependency-import', args=[cfg.id]),
                      {'lockfile': SimpleUploadedFile(name, content)}, HTTP_HX_REQUEST='true')

    def test_import_then_show_vulnerable_libraries(self):
        org, cfg = _make_cfg()
        c = _client(org)
        resp = self._upload(c, cfg, 'requirements.txt', b'django==3.2.0\nsafe==1.0\n')
        html = resp.content.decode()
        assert resp.status_code == 200 and 'Imported 2 libraries from requirements.txt' in html
        assert 'Not checked for vulnerabilities yet.' in html
        with mock.patch('asset_manager.vulns._request', side_effect=_fake_osv({'django': [DJANGO_VULN]})):
            vulns.refresh_vulns(force=True)
        html = c.get(reverse('application-list-all')).content.decode()
        assert 'Vulnerable libraries: 1' in html and 'high or critical: 1' in html and 'known exploited: 1' in html
        assert 'https://osv.dev/vulnerability/GHSA-aaaa' in html and 'noopener' in html
        assert 'Fixed in' in html and '2.2.28' in html
        assert 'Show 1 other libraries' in html

    def test_clean_libraries_say_so(self):
        org, cfg = _make_cfg()
        c = _client(org)
        self._upload(c, cfg, 'requirements.txt', b'safe==1.0\n')
        with mock.patch('asset_manager.vulns._request', side_effect=_fake_osv({})):
            vulns.refresh_vulns(force=True)
        html = c.get(reverse('application-list-all')).content.decode()
        assert 'No known vulnerabilities in the 1 checked libraries.' in html

    def test_bad_file_is_a_clean_message_and_stores_nothing(self):
        org, cfg = _make_cfg()
        c = _client(org)
        html = self._upload(c, cfg, 'Dockerfile', b'FROM python').content.decode()
        assert 'Could not read this file' in html
        assert not cfg.dependencies.exists()

    def test_cannot_import_into_another_orgs_environment(self):
        org_a, _ = _make_cfg('a')
        _, cfg_b = _make_cfg('b')
        resp = self._upload(_client(org_a), cfg_b, 'requirements.txt', b'django==3.2.0\n')
        assert resp.status_code == 404
        assert not cfg_b.dependencies.exists()

    def test_requires_login_and_post(self):
        _, cfg = _make_cfg()
        url = reverse('dependency-import', args=[cfg.id])
        assert Client().post(url, {}).status_code in (302, 204)
        org2, _ = _make_cfg('z')
        assert _client(org2, 'z').get(url).status_code == 405

    def test_uploaded_file_is_not_kept_anywhere(self, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        org, cfg = _make_cfg()
        self._upload(_client(org), cfg, 'requirements.txt', b'django==3.2.0\n')
        assert list(tmp_path.iterdir()) == []
