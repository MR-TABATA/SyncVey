"""
eol_refresh.py
--------------
endoflife.date から EOL データを取得して DB（EolSnapshot）に保存する。

- 既定では無効（settings.EOL_REFRESH_ENABLED=false）。明示的に有効化したときだけ外部通信する。
- 取得対象:
    base_products()（既知の固定セット）
    ∪ EOL_REFRESH_DYNAMIC=true なら DB の依存パッケージ名から動的導出
- セキュリティ: https + endoflife.date 限定、slug はトークン検証（SSRF/パスインジェクション対策）。
"""
import json
import logging
import re
import urllib.request
import urllib.error

from django.conf import settings

from .eol_data import base_products, canonical, invalidate_cache

logger = logging.getLogger(__name__)

API_HOST   = "endoflife.date"
API_TMPL   = "https://endoflife.date/api/{slug}.json"
_SLUG_RE   = re.compile(r'^[a-z0-9][a-z0-9.\-]*$')
_TIMEOUT   = 10
_USER_AGENT = "SyncVey-EOL/1.0 (+https://github.com/MR-TABATA/SyncVey)"


def _target_products() -> list[str]:
    """取得対象の product slug 一覧を組み立てる（base ∪ 動的導出）。"""
    products = set(base_products())

    if settings.EOL_REFRESH_DYNAMIC:
        try:
            from .models import AppDependency
            names = AppDependency.objects.values_list('name', flat=True).distinct()
            for n in names:
                canon = canonical(n or '')
                if canon:  # None（EOL概念なし）は除外
                    products.add(canon)
        except Exception:
            logger.exception("EOL: failed to derive dynamic product list; using base set only")

    # slug として安全なものだけ
    return sorted(p for p in products if _SLUG_RE.match(p))


def _normalize_eol(value) -> str | None:
    """endoflife.date の eol 値 -> 'YYYY-MM-DD' | None（None=サポート中）。"""
    if value is True:
        return "1970-01-01"          # EOL だが日付不明 → 過去日でEOL扱い
    if value is False or value is None:
        return None                  # サポート中
    if isinstance(value, str) and re.match(r'^\d{4}-\d{2}-\d{2}', value):
        return value[:10]
    return None


def _fetch_product(slug: str) -> dict | None:
    """1プロダクト分を取得して {cycle: eol_or_None} を返す。失敗時 None。"""
    url = API_TMPL.format(slug=slug)
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT}, method="GET")
    try:
        # nosec B310: URL は固定テンプレ + 検証済み slug（https + endoflife.date 限定）
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # nosec B310
            if resp.status != 200:
                return None
            cycles = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code != 404:
            logger.warning("EOL fetch failed (%s): %s", slug, e)
        return None
    except (urllib.error.URLError, json.JSONDecodeError, ValueError) as e:
        logger.warning("EOL fetch failed (%s): %s", slug, e)
        return None

    if not isinstance(cycles, list):
        return None
    out = {}
    for c in cycles:
        if isinstance(c, dict) and 'cycle' in c:
            out[str(c['cycle'])] = _normalize_eol(c.get('eol'))
    return out or None


def refresh_eol(force: bool = False) -> dict:
    """
    EOL データを取得して EolSnapshot に保存する。
    Returns: {'ok': bool, 'skipped': bool, 'products': int, 'failed': int}
    """
    if not settings.EOL_REFRESH_ENABLED and not force:
        logger.info("EOL refresh skipped (EOL_REFRESH_ENABLED=false).")
        return {'ok': False, 'skipped': True, 'products': 0, 'failed': 0}

    slugs = _target_products()
    data, failed = {}, 0
    for slug in slugs:
        cycles = _fetch_product(slug)
        if cycles:
            data[slug] = cycles
        else:
            failed += 1

    if not data:
        logger.warning("EOL refresh produced no data (failed=%s).", failed)
        return {'ok': False, 'skipped': False, 'products': 0, 'failed': failed}

    from .models import EolSnapshot
    EolSnapshot.objects.create(data=data, source=API_HOST)
    invalidate_cache()
    logger.info("EOL refresh done: %s products, %s failed.", len(data), failed)
    return {'ok': True, 'skipped': False, 'products': len(data), 'failed': failed}
