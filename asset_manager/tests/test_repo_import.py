"""開発リポジトリ（GitHub）からのロックファイル取り込み。通信は mock で、実通信はしない。"""
import io
import urllib.error
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from asset_manager import repo_import
from asset_manager.models import (Application, AppEnvConfig, Environment, Membership, Organization, System)
from asset_manager.repo_import import RepoError, import_from_repo, parse_repo_url

pytestmark = pytest.mark.django_db


class TestParseRepoUrl:
    @pytest.mark.parametrize('url,expected', [
        ('https://github.com/MR-TABATA/SyncVey', ('MR-TABATA', 'SyncVey', None, '')),
        ('https://github.com/o/r/', ('o', 'r', None, '')),
        ('https://github.com/o/r.git', ('o', 'r', None, '')),
        ('  https://github.com/o/r  ', ('o', 'r', None, '')),
        ('https://github.com/o/r/tree/main', ('o', 'r', 'main', '')),
        ('https://github.com/o/r/tree/v1.2/services/api', ('o', 'r', 'v1.2', 'services/api')),
        ('https://github.com/o/my.repo-name_2', ('o', 'my.repo-name_2', None, '')),
    ])
    def test_accepts_github_https_urls(self, url, expected):
        assert parse_repo_url(url) == expected

    @pytest.mark.parametrize('url', [
        '', 'github.com/o/r', 'http://github.com/o/r',                 # https 以外
        'https://gitlab.com/o/r', 'https://bitbucket.org/o/r',          # GitHub 以外（今は未対応）
        'https://github.com.evil.example/o/r',                          # ホスト名の偽装
        'https://evil.example/https://github.com/o/r',
        'https://user:pw@github.com/o/r', 'https://github.com@evil.example/o/r',
        'https://github.com/o', 'https://github.com/o/r/extra',         # 形が違う
        'https://github.com/o/r/tree/main/../secret', 'https://github.com/o/r/tree/main//x',
        'https://169.254.169.254/latest/meta-data/', 'file:///etc/passwd',
    ])
    def test_rejects_everything_else(self, url):
        with pytest.raises(RepoError):
            parse_repo_url(url)


def _http_error(code, headers=None):
    return urllib.error.HTTPError('u', code, 'x', headers or {}, io.BytesIO(b''))


def _resp(body):
    r = mock.MagicMock()
    r.read.return_value = body
    r.__enter__.return_value = r
    return r


def _repo(files, token=''):
    """GitHub の mock: files = {'requirements.txt': b'...'}。無いものは 404。リクエストを記録する。"""
    seen = []
    def urlopen(req, timeout=None):
        seen.append(req)
        path = req.full_url.split('/contents/', 1)[1].split('?', 1)[0]
        if path in files:
            return _resp(files[path])
        raise _http_error(404)
    return urlopen, seen


def _make_cfg(name='o'):
    org = Organization.objects.create(name=name, slug=name)
    system = System.objects.create(name=f's-{name}', code=f's-{name}', organization=org)
    env = Environment.objects.create(system=system, name='prod', env_type='PROD')
    app = Application.objects.create(system=system, name='app', language='python')
    return org, AppEnvConfig.objects.create(application=app, environment=env)


def _client(org, username='u'):
    user = get_user_model().objects.create_user(username=username, password='pw')
    Membership.objects.create(user=user, organization=org, role=Membership.Role.OWNER)
    c = Client()
    c.force_login(user)
    return c


class TestImportFromRepo:
    def test_imports_every_lockfile_found_at_the_top_level(self):
        _, cfg = _make_cfg()
        urlopen, seen = _repo({'requirements.txt': b'django==3.2.0\nflask==2.0.1\n',
                               'go.mod': b'module x\n\nrequire github.com/gin-gonic/gin v1.7.0\n'})
        with mock.patch('urllib.request.urlopen', urlopen):
            result = import_from_repo(cfg, 'https://github.com/o/r')
        assert result['repo'] == 'o/r'
        assert [(f['name'], f['ecosystem'], f['total']) for f in result['files']] == [
            ('requirements.txt', 'PyPI', 2), ('go.mod', 'Go', 1)]
        assert set(cfg.dependencies.values_list('ecosystem', flat=True)) == {'PyPI', 'Go'}
        assert len(seen) == 5                                       # 5 種類の名前を、1 回ずつ見に行く

    def test_only_the_github_api_is_called_and_only_with_owner_repo_path(self):
        _, cfg = _make_cfg()
        urlopen, seen = _repo({'services/api/package-lock.json': b'{"lockfileVersion":3,"packages":{"node_modules/a":{"version":"1.0.0"}}}'})
        with mock.patch('urllib.request.urlopen', urlopen):
            import_from_repo(cfg, 'https://github.com/o/r/tree/dev/services/api')
        assert all(r.full_url.startswith('https://api.github.com/repos/o/r/contents/services/api/') for r in seen)
        assert all(r.full_url.endswith('?ref=dev') for r in seen)

    def test_the_api_host_is_the_admins_setting_never_the_typed_url(self, settings):
        _, cfg = _make_cfg()
        settings.SYNCVEY_GITHUB_API = 'https://ghe.example.internal/api/v3'
        urlopen, seen = _repo({'requirements.txt': b'a==1\n'})
        with mock.patch('urllib.request.urlopen', urlopen):
            import_from_repo(cfg, 'https://github.com/o/r')
        assert all(r.full_url.startswith('https://ghe.example.internal/api/v3/repos/o/r/contents/') for r in seen)
        with pytest.raises(RepoError):                              # 入力した URL のホストでは変えられない
            import_from_repo(cfg, 'https://ghe.example.internal/o/r')

    def test_a_token_is_sent_only_when_configured_and_only_to_github(self, settings):
        _, cfg = _make_cfg()
        urlopen, seen = _repo({'requirements.txt': b'a==1\n'})
        settings.SYNCVEY_GITHUB_TOKEN = ''
        with mock.patch('urllib.request.urlopen', urlopen):
            import_from_repo(cfg, 'https://github.com/o/r')
        assert all('Authorization' not in r.headers for r in seen)
        settings.SYNCVEY_GITHUB_TOKEN = 'ghp_secret'
        urlopen, seen = _repo({'requirements.txt': b'a==1\n'})
        with mock.patch('urllib.request.urlopen', urlopen):
            import_from_repo(cfg, 'https://github.com/o/r')
        assert all(r.headers['Authorization'] == 'Bearer ghp_secret' for r in seen)
        assert all(r.full_url.startswith('https://api.github.com/') for r in seen)

    def test_nothing_found_is_an_error_and_changes_nothing(self):
        _, cfg = _make_cfg()
        urlopen, _ = _repo({})
        with mock.patch('urllib.request.urlopen', urlopen):
            with pytest.raises(RepoError) as e:
                import_from_repo(cfg, 'https://github.com/o/r')
        assert str(e.value) == 'none' and not cfg.dependencies.exists()

    def test_an_unreadable_file_is_skipped_but_the_others_are_kept(self):
        _, cfg = _make_cfg()
        urlopen, _ = _repo({'package-lock.json': b'not json', 'requirements.txt': b'a==1\n'})
        with mock.patch('urllib.request.urlopen', urlopen):
            result = import_from_repo(cfg, 'https://github.com/o/r')
        assert [f['name'] for f in result['files']] == ['requirements.txt']

    def test_a_directory_with_a_lockfile_name_is_not_a_file(self):
        _, cfg = _make_cfg()
        urlopen, _ = _repo({'requirements.txt': b'[{"name":"x","type":"file"}]'})     # ディレクトリの一覧が返る
        with mock.patch('urllib.request.urlopen', urlopen):
            with pytest.raises(RepoError) as e:
                import_from_repo(cfg, 'https://github.com/o/r')
        assert str(e.value) == 'none'

    @pytest.mark.parametrize('error,expected', [
        (lambda: _http_error(403, {'X-RateLimit-Remaining': '0'}), 'rate'),
        (lambda: _http_error(403), 'auth'),
        (lambda: _http_error(401), 'auth'),
        (lambda: _http_error(500), 'fetch'),
        (lambda: urllib.error.URLError('down'), 'network'),
    ])
    def test_failures_become_clean_errors(self, error, expected):
        _, cfg = _make_cfg()
        with mock.patch('urllib.request.urlopen', side_effect=error()):
            with pytest.raises(RepoError) as e:
                import_from_repo(cfg, 'https://github.com/o/r')
        assert str(e.value) == expected

    def test_a_huge_file_is_refused(self):
        _, cfg = _make_cfg()
        urlopen, _ = _repo({'requirements.txt': b'a' * (5 * 1024 * 1024 + 5)})
        with mock.patch('urllib.request.urlopen', urlopen):
            with pytest.raises(RepoError) as e:
                import_from_repo(cfg, 'https://github.com/o/r')
        assert str(e.value) == 'size'


class TestScreen:
    def _post(self, c, cfg, url):
        return c.post(reverse('dependency-import-repo', args=[cfg.id]), {'repo_url': url}, HTTP_HX_REQUEST='true')

    def test_import_then_the_url_is_remembered_on_the_app(self):
        org, cfg = _make_cfg()
        urlopen, _ = _repo({'requirements.txt': b'django==3.2.0\n'})
        with mock.patch('urllib.request.urlopen', urlopen):
            html = self._post(_client(org), cfg, 'https://github.com/o/r').content.decode()
        assert 'Imported from o/r: requirements.txt (1). It will be synced again every day.' in html
        cfg.refresh_from_db()
        assert cfg.repo_url == 'https://github.com/o/r' and cfg.repo_synced_at and cfg.repo_sync_error == ''
        assert cfg.application.repository_url == 'https://github.com/o/r'
        # 次に開くと、その URL が入力欄に入り、同期の状態が出ている
        page = _client(org, 'v').get(reverse('application-list-all')).content.decode()
        assert 'value="https://github.com/o/r"' in page and 'Synced from the repository every day' in page

    def test_bad_url_says_what_to_enter_and_stores_nothing(self):
        org, cfg = _make_cfg()
        with mock.patch('urllib.request.urlopen') as u:
            html = self._post(_client(org), cfg, 'https://evil.example/o/r').content.decode()
        u.assert_not_called()                                      # 検証に落ちたら、通信しない
        assert 'Enter a GitHub repository URL' in html
        cfg.application.refresh_from_db()
        assert not cfg.application.repository_url and not cfg.dependencies.exists()

    def test_not_found_message_mentions_the_token_for_private_repos(self):
        org, cfg = _make_cfg()
        urlopen, _ = _repo({})
        with mock.patch('urllib.request.urlopen', urlopen):
            html = self._post(_client(org), cfg, 'https://github.com/o/private').content.decode()
        assert 'No lockfile was found' in html and 'SYNCVEY_GITHUB_TOKEN' in html
        cfg.application.refresh_from_db()
        assert not cfg.application.repository_url               # 失敗した URL は覚えない

    def test_cannot_import_into_another_orgs_environment(self):
        org_a, _ = _make_cfg('a')
        _, cfg_b = _make_cfg('b')
        with mock.patch('urllib.request.urlopen') as u:
            resp = self._post(_client(org_a), cfg_b, 'https://github.com/o/r')
        assert resp.status_code == 404
        u.assert_not_called()
        assert not cfg_b.dependencies.exists()

    def test_requires_login_and_post(self):
        _, cfg = _make_cfg()
        url = reverse('dependency-import-repo', args=[cfg.id])
        assert Client().post(url, {'repo_url': 'https://github.com/o/r'}).status_code in (302, 204)
        org2, _ = _make_cfg('z')
        assert _client(org2, 'z').get(url).status_code == 405


class TestDailySync:
    """リポジトリを登録した環境は、毎日自動で同期する。手で更新するものは陳腐化するため。"""

    def _registered(self, name='o', url='https://github.com/o/r'):
        org, cfg = _make_cfg(name)
        cfg.repo_url = url
        cfg.save()
        return org, cfg

    def test_every_registered_environment_is_re_imported(self):
        _, cfg = self._registered()
        _, plain = _make_cfg('plain')                                   # 登録なし -> 触らない
        urlopen, seen = _repo({'requirements.txt': b'django==3.2.0\n'})
        with mock.patch('urllib.request.urlopen', urlopen):
            result = repo_import.refresh_repos()
        assert (result['synced'], result['failed']) == (1, 0)
        assert cfg.dependencies.filter(name='django').exists() and not plain.dependencies.exists()
        cfg.refresh_from_db()
        assert cfg.repo_synced_at and cfg.repo_sync_error == ''

    def test_the_ledger_follows_the_repository_when_it_changes(self):
        _, cfg = self._registered()
        with mock.patch('urllib.request.urlopen', _repo({'requirements.txt': b'django==3.2.0\nold==1.0\n'})[0]):
            repo_import.refresh_repos()
        with mock.patch('urllib.request.urlopen', _repo({'requirements.txt': b'django==4.2.0\n'})[0]):
            repo_import.refresh_repos()
        assert sorted(cfg.dependencies.values_list('name', 'version')) == [('django', '4.2.0')]   # 古い行は残らない

    def test_a_failure_is_recorded_and_the_last_good_libraries_stay(self):
        org, cfg = self._registered()
        with mock.patch('urllib.request.urlopen', _repo({'requirements.txt': b'django==3.2.0\n'})[0]):
            repo_import.refresh_repos()
        good_at = AppEnvConfig.objects.get(pk=cfg.pk).repo_synced_at
        with mock.patch('urllib.request.urlopen', side_effect=_http_error(403)):
            result = repo_import.refresh_repos()
        assert (result['synced'], result['failed']) == (0, 1)
        cfg.refresh_from_db()
        assert cfg.repo_sync_error == 'auth' and cfg.repo_synced_at == good_at      # いつまで新しいかが分かる
        assert cfg.dependencies.filter(name='django').exists()                       # 古い台帳は消さない
        html = _client(org).get(reverse('application-list-all')).content.decode()
        assert 'Sync failed' in html and 'GitHub refused the request' in html and 'last successful sync' in html

    def test_one_failure_does_not_stop_the_others(self):
        _, a = self._registered('a', 'https://github.com/o/a')
        _, b = self._registered('b', 'https://github.com/o/b')
        def urlopen(req, timeout=None):
            if '/o/a/' in req.full_url:
                raise _http_error(500)
            if req.full_url.split('/contents/', 1)[1].startswith('requirements.txt'):
                return _resp(b'flask==2.0.1\n')
            raise _http_error(404)
        with mock.patch('urllib.request.urlopen', urlopen):
            result = repo_import.refresh_repos()
        assert (result['synced'], result['failed']) == (1, 1)
        assert b.dependencies.filter(name='flask').exists() and not a.dependencies.exists()

    def test_nothing_is_called_without_a_registration(self):
        _make_cfg('plain')
        with mock.patch('urllib.request.urlopen') as u:
            repo_import.refresh_repos()
        u.assert_not_called()

    def test_can_be_switched_off(self, settings):
        self._registered()
        settings.REPO_REFRESH_ENABLED = False
        with mock.patch('urllib.request.urlopen') as u:
            result = repo_import.refresh_repos()
        assert result['skipped'] is True
        u.assert_not_called()

    def test_a_bad_stored_url_is_a_recorded_failure_not_a_crash(self):
        _, cfg = self._registered(url='https://evil.example/o/r')
        with mock.patch('urllib.request.urlopen') as u:
            result = repo_import.refresh_repos()
        u.assert_not_called()
        cfg.refresh_from_db()
        assert result['failed'] == 1 and cfg.repo_sync_error == 'url'

    def test_pressing_sync_now_on_a_registered_repo_records_a_failure(self):
        org, cfg = self._registered()
        with mock.patch('urllib.request.urlopen', side_effect=_http_error(500)):
            _client(org).post(reverse('dependency-import-repo', args=[cfg.id]), {'repo_url': 'https://github.com/o/r'}, HTTP_HX_REQUEST='true')
        cfg.refresh_from_db()
        assert cfg.repo_sync_error == 'fetch'

    def test_the_daily_job_is_registered_unless_switched_off(self, settings):
        from django.db import connection
        from asset_manager import scheduler

        class FakeScheduler:
            running = False
            def __init__(self, *a, **k): self.ids = []
            def add_jobstore(self, *a, **k): pass
            def add_job(self, func, **kw): self.ids.append(kw['id'])
            def start(self): pass

        for enabled in (True, False):
            settings.REPO_REFRESH_ENABLED = enabled
            with mock.patch.object(scheduler, 'BackgroundScheduler', FakeScheduler), \
                    mock.patch.object(scheduler, 'DjangoJobStore'), \
                    mock.patch.object(scheduler, '_scheduler', None), \
                    mock.patch.object(connection.introspection, 'table_names', return_value=['django_apscheduler_djangojob']):
                scheduler.start()
                ids = scheduler._scheduler.ids
            assert ('repo_refresh_daily' in ids) is enabled, (enabled, ids)
