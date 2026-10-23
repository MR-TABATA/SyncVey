"""開発リポジトリ（GitHub）から、ロックファイルを取り込む。

一度リポジトリを登録すれば、毎日自動で同期する（手で更新するものは陳腐化する）。結果（最終同期の日時・失敗）は
環境に記録し、画面に出す。失敗したまま古い台帳が黙って残らないようにするため。押して今すぐ取り込むこともできる。

- 取りに行く先は、管理者が環境変数で決める GitHub API（既定 api.github.com）に固定。利用者が入れた URL は
  「owner / repo / ブランチ / ディレクトリ」に分解するだけで、そのまま取りに行かない（内部のサーバーへ向けた URL を
  踏ませない = SSRF 対策）。
- 非公開リポジトリは、環境変数 SYNCVEY_GITHUB_TOKEN（読み取り専用のトークン）があれば使う。DB には保存しない。
  トークンは api.github.com にしか送らず、エラー文にも出さない。
- 見に行くファイルは、リポジトリ直下（または指定ディレクトリ）の、既知のロックファイルだけ。
"""
import logging
import re
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.utils import timezone

from .dependency_import import import_lockfile
from .lockfiles import MAX_BYTES, LockfileError

logger = logging.getLogger(__name__)

_TIMEOUT = 15
_USER_AGENT = "SyncVey-RepoImport/1.0 (+https://github.com/MR-TABATA/SyncVey)"
# リポジトリ直下で探すロックファイル（lockfiles.parse が読めるもの）
LOCKFILE_NAMES = ('requirements.txt', 'package-lock.json', 'composer.lock', 'Gemfile.lock', 'go.mod')

_NAME = r'[A-Za-z0-9_.-]+'
_URL = re.compile(
    rf'^https://github\.com/(?P<owner>{_NAME})/(?P<repo>{_NAME}?)(?:\.git)?'
    rf'(?:/tree/(?P<ref>[^/]+)(?:/(?P<path>[A-Za-z0-9_./@+-]*))?)?/?$'
)


class RepoError(Exception):
    """ユーザーに見せてよい文言を持つ失敗。"""


def parse_repo_url(url: str):
    """URL -> (owner, repo, ref | None, subdir)。GitHub の https URL 以外は受け付けない。"""
    m = _URL.match((url or '').strip())
    if not m:
        raise RepoError('url')
    raw = m.group('path') or ''
    if raw.startswith('/') or '//' in raw:                 # 「/tree/main//x」のような崩れた形は受け付けない
        raise RepoError('url')
    subdir = raw.strip('/')
    if subdir and any(part in ('.', '..') for part in subdir.split('/')):
        raise RepoError('url')
    return m.group('owner'), m.group('repo'), m.group('ref'), subdir


def _fetch(owner, repo, ref, path):
    """1 ファイルの中身。無ければ None。それ以外の失敗は RepoError。"""
    qs = f"?ref={urllib.parse.quote(ref)}" if ref else ''
    url = f"{settings.SYNCVEY_GITHUB_API}/repos/{owner}/{repo}/contents/{urllib.parse.quote(path)}{qs}"
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/vnd.github.raw+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    token = getattr(settings, 'SYNCVEY_GITHUB_TOKEN', '')
    if token:
        headers["Authorization"] = f"Bearer {token}"      # 設定した GitHub API にだけ送る
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        # nosec B310: URL は API 固定 + 検証済みの owner / repo / path（https）
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # nosec B310
            body = resp.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        if e.code == 403 and e.headers.get('X-RateLimit-Remaining') == '0':
            raise RepoError('rate')
        if e.code in (401, 403):
            raise RepoError('auth')
        logger.warning("GitHub returned %s for %s/%s", e.code, owner, repo)
        raise RepoError('fetch')
    except (urllib.error.URLError, OSError):
        logger.warning("GitHub request failed for %s/%s", owner, repo)
        raise RepoError('network')
    if len(body) > MAX_BYTES:
        raise RepoError('size')
    if body.lstrip()[:1] == b'[':          # 同じ名前のディレクトリだった（一覧が返る）
        return None
    return body


def import_from_repo(cfg, url: str) -> dict:
    """リポジトリ直下のロックファイルを、すべて取り込む。

    Returns: {'repo': 'owner/repo', 'files': [{'name', 'ecosystem', 'total', 'added', 'removed'}]}
    取れたものが 1 つも無ければ RepoError('none')。
    """
    owner, repo, ref, subdir = parse_repo_url(url)
    results = []
    for name in LOCKFILE_NAMES:
        path = f"{subdir}/{name}" if subdir else name
        body = _fetch(owner, repo, ref, path)
        if body is None:
            continue
        try:
            r = import_lockfile(cfg, name, body)
        except LockfileError:
            continue                        # 読めない中身は飛ばす（ほかのファイルは続ける）
        results.append({'name': name, **r})
    if not results:
        raise RepoError('none')
    return {'repo': f"{owner}/{repo}", 'files': results}


def mark_synced(cfg, url: str):
    """成功: 元の URL・日時を環境に覚え、失敗の記録を消す。アプリの repository_url が空なら、それも埋める。"""
    cfg.repo_url, cfg.repo_synced_at, cfg.repo_sync_error = url, timezone.now(), ''
    cfg.save(update_fields=['repo_url', 'repo_synced_at', 'repo_sync_error', 'updated_at'])
    app = cfg.application
    if not app.repository_url:
        app.repository_url = url
        app.save(update_fields=['repository_url', 'updated_at'])


def mark_failed(cfg, code: str):
    """失敗: 理由の種類だけ記録する（最後に成功した日時は残す。いつから古いかが分かるように）。"""
    cfg.repo_sync_error = code[:20]
    cfg.save(update_fields=['repo_sync_error', 'updated_at'])


def refresh_repos(force: bool = False) -> dict:
    """リポジトリを登録した環境すべてを、同期し直す（毎日のジョブ）。

    Returns: {'ok', 'skipped', 'synced', 'failed'}。1 つ失敗しても、ほかは続ける。
    """
    if not settings.REPO_REFRESH_ENABLED and not force:
        logger.info("Repository sync skipped (REPO_REFRESH_ENABLED=false).")
        return {'ok': False, 'skipped': True, 'synced': 0, 'failed': 0}
    from .models import AppEnvConfig
    synced = failed = 0
    for cfg in AppEnvConfig.objects.exclude(repo_url='').select_related('application'):
        try:
            import_from_repo(cfg, cfg.repo_url)
        except RepoError as e:
            mark_failed(cfg, str(e))
            failed += 1
            continue
        mark_synced(cfg, cfg.repo_url)
        synced += 1
    return {'ok': True, 'skipped': False, 'synced': synced, 'failed': failed}
