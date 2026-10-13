"""ライブラリの脆弱性: OSV 取得（通信はモック）・ロックファイル取り込み・画面・権限。"""
import json
import urllib.parse
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


# ---------------------------------------------------------------------------
# RDS のエンジン（NVD）
# ---------------------------------------------------------------------------

def _cve(cid, score=None, kev=None, desc='A flaw.', status='Analyzed'):
    metrics = {'ssvcV203': [{'cvssData': None}]}
    if score is not None:
        metrics['cvssMetricV31'] = [{'cvssData': {'baseScore': score}}]
    cve = {'id': cid, 'vulnStatus': status, 'descriptions': [{'lang': 'es', 'value': 'x'}, {'lang': 'en', 'value': desc}],
           'metrics': metrics}
    if kev:
        cve['cisaExploitAdd'] = '2026-01-01'
    return cve


def _nvd_page(cves, total=None, start=0):
    return {'resultsPerPage': len(cves), 'startIndex': start, 'totalResults': total if total is not None else len(cves),
            'vulnerabilities': [{'cve': c} for c in cves]}


def _rds(env, name, engine, version, **extra):
    from asset_manager.models import Asset
    return Asset.objects.create(environment=env, name=name, provider='AWS', asset_type='RDS', asset_category='DATABASE',
                                cloud_id=name, raw_data={'engine': engine, 'engine_version': version, **extra})


def _env():
    org = Organization.objects.create(name='secret-org', slug='secret-org')
    system = System.objects.create(name='secret-system', code='ss', organization=org)
    return org, Environment.objects.create(system=system, name='prod', env_type='PROD')


class TestAssetCveTarget:
    def test_maps_rds_postgres_and_mysql_only(self):
        t = vulns.asset_cve_target
        assert t('RDS', {'engine': 'postgres', 'engine_version': '11.22'}) == ('NVD', 'postgresql:postgresql', '11.22')
        assert t('RDS', {'engine': 'mysql', 'engine_version': '8.0.35'}) == ('NVD', 'oracle:mysql', '8.0.35')
        # Aurora は版が上流と一致しない・未対応のエンジン・版なし・RDS 以外は対象外
        assert t('RDS', {'engine': 'aurora-mysql', 'engine_version': '5.7.mysql_aurora.2.11.2'}) is None
        assert t('RDS', {'engine': 'aurora-postgresql', 'engine_version': '15.4'}) is None
        assert t('RDS', {'engine': 'oracle-ee', 'engine_version': '19'}) is None
        assert t('RDS', {'engine': 'postgres'}) is None
        assert t('EC2', {'engine': 'postgres', 'engine_version': '11.22'}) is None
        assert t('RDS', None) is None


    def test_elasticache_needs_the_patch_number(self):
        t = vulns.asset_cve_target
        assert t('ELASTICACHE', {'engine': 'redis', 'engine_version': '5.0.6'}) == ('NVD', 'redis:redis', '5.0.6')
        assert t('ELASTICACHE', {'engine': 'memcached', 'engine_version': '1.6.6'}) == ('NVD', 'memcached:memcached', '1.6.6')
        # 7.1 のようにパッチ番号がない版は、AWS が裏で更新する。当てると直った CVE を拾うので調べない。
        assert t('ELASTICACHE', {'engine': 'redis', 'engine_version': '7.1'}) is None
        assert t('ELASTICACHE', {'engine': 'memcached', 'engine_version': '1.6'}) is None
        # Valkey は NVD に無い。RDS の種別には ElastiCache のエンジンを混ぜない。
        assert t('ELASTICACHE', {'engine': 'valkey', 'engine_version': '7.2.4'}) is None
        assert t('RDS', {'engine': 'redis', 'engine_version': '5.0.6'}) is None
        assert t('ELASTICACHE', {'engine': 'postgres', 'engine_version': '11.22'}) is None


class TestRefreshNvd:
    def _run(self, pages_by_version, kev=()):
        sent = []
        def nvd(url):
            sent.append(url)
            for version, page in pages_by_version.items():
                if f':{version}:' in urllib.parse.unquote(url):
                    return page(url) if callable(page) else page
            return _nvd_page([])
        def osv(url, payload=None):
            if url == vulns.KEV_URL:
                return {'vulnerabilities': [{'cveID': c} for c in kev]}
            raise AssertionError(url)
        with mock.patch('asset_manager.vulns._nvd_request', side_effect=nvd), \
                mock.patch('asset_manager.vulns._nvd_pause'), \
                mock.patch('asset_manager.vulns._request', side_effect=osv):
            return vulns.refresh_vulns(force=True), sent

    def test_stores_cves_with_severity_kev_and_link(self):
        _, env = _env()
        _rds(env, 'db', 'postgres', '11.22')
        result, sent = self._run({'11.22': _nvd_page([_cve('CVE-2026-1', 8.8, desc='Integer wraparound.'),
                                                      _cve('CVE-2026-2', 4.3),
                                                      _cve('CVE-2026-3', 9.8, kev=True),
                                                      _cve('CVE-2026-4')])})
        assert (result['engines'], result['engines_vulnerable'], result['ok']) == (1, 1, True)
        rec = VulnRecord.objects.get(ecosystem='NVD', name='postgresql:postgresql', version='11.22')
        sev = {a['id']: a['severity'] for a in rec.advisories}
        assert sev == {'CVE-2026-3': 'CRITICAL', 'CVE-2026-1': 'HIGH', 'CVE-2026-2': 'MEDIUM', 'CVE-2026-4': 'UNKNOWN'}
        assert rec.advisories[0]['id'] == 'CVE-2026-3'                      # 重大度の高い順
        assert rec.max_severity == 'CRITICAL' and rec.has_kev is True
        top = next(a for a in rec.advisories if a['id'] == 'CVE-2026-1')
        assert top['summary'] == 'Integer wraparound.'                      # 英語の説明を取る
        assert top['url'] == 'https://nvd.nist.gov/vuln/detail/CVE-2026-1'

    def test_sends_only_the_cpe_and_no_names(self):
        _, env = _env()
        _rds(env, 'my-secret-db', 'mysql', '8.0.35')
        _, sent = self._run({'8.0.35': _nvd_page([])})
        assert len(sent) == 1
        assert 'cpeName=cpe%3A2.3%3Aa%3Aoracle%3Amysql%3A8.0.35' in sent[0] and sent[0].endswith('&isVulnerable')
        assert 'secret' not in sent[0]

    def test_kev_from_the_catalogue_also_marks(self):
        _, env = _env()
        _rds(env, 'db', 'postgres', '16.2')
        self._run({'16.2': _nvd_page([_cve('CVE-2026-9', 7.5)])}, kev=('CVE-2026-9',))
        assert VulnRecord.objects.get(version='16.2').has_kev is True

    def test_follows_pagination(self):
        _, env = _env()
        _rds(env, 'db', 'postgres', '16.2')
        def pages(url):
            if 'startIndex=0' in url:
                return _nvd_page([_cve('CVE-1', 5), _cve('CVE-2', 5)], total=3, start=0)
            return _nvd_page([_cve('CVE-3', 5)], total=3, start=2)
        self._run({'16.2': pages})
        assert len(VulnRecord.objects.get(version='16.2').advisories) == 3

    def test_only_live_non_aurora_rds_are_queried_and_versions_are_deduplicated(self):
        from django.utils import timezone
        _, env = _env()
        _rds(env, 'a', 'postgres', '16.2')
        _rds(env, 'b', 'postgres', '16.2')                                  # 同じ版は 1 回だけ
        _rds(env, 'c', 'aurora-postgresql', '15.4')
        gone = _rds(env, 'd', 'mysql', '5.7.44')
        gone.missing_since = timezone.now()
        gone.save()
        result, sent = self._run({})
        assert result['engines'] == 1 and len(sent) == 1

    def test_nvd_failure_keeps_the_previous_result(self):
        import urllib.error
        _, env = _env()
        _rds(env, 'db', 'postgres', '16.2')
        VulnRecord.objects.create(ecosystem='NVD', name='postgresql:postgresql', version='16.2',
                                  advisories=[{'id': 'CVE-OLD'}], max_severity='HIGH')
        with mock.patch('asset_manager.vulns._nvd_request', side_effect=urllib.error.URLError('429')), \
                mock.patch('asset_manager.vulns._nvd_pause'), \
                mock.patch('asset_manager.vulns._request', return_value={'vulnerabilities': []}):
            result = vulns.refresh_vulns(force=True)
        assert result['ok'] is False and result['engines_failed'] == 1
        assert VulnRecord.objects.get(version='16.2').advisories == [{'id': 'CVE-OLD'}]

    def test_osv_down_does_not_stop_nvd_and_keeps_its_records(self):
        import urllib.error
        _, env = _env()
        _rds(env, 'db', 'postgres', '16.2')
        org2, cfg = _make_cfg('lib')
        import_lockfile(cfg, 'requirements.txt', b'django==3.2.0\n')
        VulnRecord.objects.create(ecosystem='PyPI', name='django', version='3.2.0', advisories=[{'id': 'X'}])
        def osv(url, payload=None):
            if url == vulns.KEV_URL:
                return {'vulnerabilities': []}
            raise urllib.error.URLError('down')
        with mock.patch('asset_manager.vulns._request', side_effect=osv), \
                mock.patch('asset_manager.vulns._nvd_request', return_value=_nvd_page([_cve('CVE-1', 8)])), \
                mock.patch('asset_manager.vulns._nvd_pause'):
            result = vulns.refresh_vulns(force=True)
        assert result['ok'] is False
        assert VulnRecord.objects.get(ecosystem='NVD').advisories[0]['id'] == 'CVE-1'
        assert VulnRecord.objects.get(name='django').advisories == [{'id': 'X'}]   # OSV 側の前回結果は消えない

    def test_records_for_engines_no_longer_in_the_ledger_are_dropped(self):
        _, env = _env()
        _rds(env, 'db', 'postgres', '16.2')
        VulnRecord.objects.create(ecosystem='NVD', name='postgresql:postgresql', version='9.6.1')
        self._run({})
        assert not VulnRecord.objects.filter(version='9.6.1').exists()

    def test_api_key_is_sent_only_when_configured(self, settings):
        captured = {}
        def fake_urlopen(req, timeout=None):
            captured['headers'] = dict(req.header_items())
            resp = mock.MagicMock()
            resp.read.return_value = b'{}'
            resp.__enter__.return_value = resp
            return resp
        settings.NVD_API_KEY = ''
        with mock.patch('urllib.request.urlopen', fake_urlopen):
            vulns._nvd_request('https://example.invalid/')
        assert 'Apikey' not in captured['headers']
        settings.NVD_API_KEY = 'k-123'
        with mock.patch('urllib.request.urlopen', fake_urlopen):
            vulns._nvd_request('https://example.invalid/')
        assert captured['headers']['Apikey'] == 'k-123'

    def test_pause_is_longer_without_a_key(self, settings):
        with mock.patch('asset_manager.vulns.time.sleep') as sleep:
            settings.NVD_API_KEY = ''
            vulns._nvd_pause()
            settings.NVD_API_KEY = 'k'
            vulns._nvd_pause()
        assert [c.args[0] for c in sleep.call_args_list] == [6.5, 0.7]


class TestElastiCacheRefresh:
    def test_redis_cluster_is_looked_up_by_its_cpe(self):
        from asset_manager.models import Asset
        _, env = _env()
        Asset.objects.create(environment=env, name='cache', provider='AWS', asset_type='ELASTICACHE',
                             asset_category='DATABASE', cloud_id='cache',
                             raw_data={'engine': 'redis', 'engine_version': '5.0.6'})
        Asset.objects.create(environment=env, name='cache2', provider='AWS', asset_type='ELASTICACHE',
                             asset_category='DATABASE', cloud_id='cache2',
                             raw_data={'engine': 'redis', 'engine_version': '7.1'})   # パッチ番号なし -> 調べない
        sent = []
        def nvd(url):
            sent.append(urllib.parse.unquote(url))
            return _nvd_page([_cve('CVE-2026-77', 7.5, desc='Redis flaw. More.')])
        with mock.patch('asset_manager.vulns._nvd_request', side_effect=nvd), \
                mock.patch('asset_manager.vulns._nvd_pause'), \
                mock.patch('asset_manager.vulns._request', return_value={'vulnerabilities': []}):
            result = vulns.refresh_vulns(force=True)
        assert (result['engines'], result['engines_vulnerable']) == (1, 1)
        assert len(sent) == 1 and 'cpe:2.3:a:redis:redis:5.0.6:' in sent[0]
        rec = VulnRecord.objects.get(ecosystem='NVD', name='redis:redis')
        assert (rec.version, rec.max_severity, rec.advisories[0]['summary']) == ('5.0.6', 'HIGH', 'Redis flaw.')

    def test_detail_panel_for_a_cluster(self):
        from asset_manager.models import Asset
        org, env = _env()
        a = Asset.objects.create(environment=env, name='cache', provider='AWS', asset_type='ELASTICACHE',
                                 asset_category='DATABASE', cloud_id='cache',
                                 raw_data={'engine': 'redis', 'engine_version': '5.0.6'})
        VulnRecord.objects.create(ecosystem='NVD', name='redis:redis', version='5.0.6', max_severity='HIGH',
                                  advisories=[{'id': 'CVE-2026-77', 'severity': 'HIGH', 'summary': 'x', 'kev': False,
                                               'url': 'https://nvd.nist.gov/vuln/detail/CVE-2026-77', 'fixed': [], 'aliases': []}])
        html = _client(org).get(reverse('asset-detail', args=[a.id])).content.decode()
        assert 'Known vulnerabilities' in html and 'in the managed service' in html


class TestRdsScreen:
    def _page(self, asset_vulns=True):
        org, env = _env()
        db = _rds(env, 'db', 'postgres', '11.22', instance_class='db.t3.micro')
        aurora = _rds(env, 'au', 'aurora-postgresql', '15.4')
        if asset_vulns:
            VulnRecord.objects.create(ecosystem='NVD', name='postgresql:postgresql', version='11.22', max_severity='CRITICAL',
                                      has_kev=True, advisories=[
                {'id': 'CVE-2026-3', 'severity': 'CRITICAL', 'summary': 'Bad thing.', 'kev': True,
                 'url': 'https://nvd.nist.gov/vuln/detail/CVE-2026-3', 'fixed': [], 'aliases': []}])
        return _client(org), db, aurora

    def test_detail_panel_lists_cves_and_says_it_is_the_upstream_version(self):
        c, db, aurora = self._page()
        html = c.get(reverse('asset-detail', args=[db.id])).content.decode()
        assert 'Known vulnerabilities' in html and '1 · CRITICAL' in html and 'Known exploited' in html
        assert 'https://nvd.nist.gov/vuln/detail/CVE-2026-3' in html and 'noopener' in html
        assert 'Matched against the upstream version in NVD' in html
        assert 'Known vulnerabilities' not in c.get(reverse('asset-detail', args=[aurora.id])).content.decode()

    def test_list_badge(self):
        c, db, aurora = self._page()
        html = c.get(reverse('asset-list')).content.decode()
        assert '1 known vulnerabilities · CRITICAL' in html
        assert html.count('1 known vulnerabilities · CRITICAL') == 2         # 表とカードに 1 回ずつ（Aurora には付かない）

    def test_nothing_shown_until_checked(self):
        c, db, _ = self._page(asset_vulns=False)
        assert 'Known vulnerabilities' not in c.get(reverse('asset-detail', args=[db.id])).content.decode()


class TestShortSummary:
    def test_first_sentence(self):
        assert vulns._short('Integer wraparound in X. This may execute code. More.') == 'Integer wraparound in X.'

    def test_long_text_is_cut_at_a_word_with_an_ellipsis(self):
        out = vulns._short('word ' * 80)
        assert out.endswith('…') and len(out) <= 201 and not out.endswith(' …') and 'wor…' not in out

    def test_short_text_is_untouched_and_whitespace_is_folded(self):
        assert vulns._short('  A   short\nflaw  ') == 'A short flaw'

    def test_cve_summary_uses_it(self):
        a = vulns._cve_advisory(_cve('CVE-1', 5, desc='Bad thing happens. Then more detail follows. ' + 'x ' * 400), set())
        assert a['summary'] == 'Bad thing happens.'

    def test_abbreviations_do_not_end_a_sentence(self):
        text = 'Symlink following allows an origin superuser to overwrite local files, e.g. /var/lib/x, that hijack the account. More text.'
        assert vulns._short(text) == 'Symlink following allows an origin superuser to overwrite local files, e.g. /var/lib/x, that hijack the account.'
        assert vulns._short('Use X, i.e. the old one, to Y. Then Z.') == 'Use X, i.e. the old one, to Y.'
