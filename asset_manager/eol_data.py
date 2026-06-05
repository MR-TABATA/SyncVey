"""
EOL (End of Life) database for common middleware and runtimes.

判定は次の優先順で行う:
  1. DB の最新 EolSnapshot（endoflife.date から取得したデータ）
  2. 下記ハードコード辞書 _EOL（オフライン/未取得時のフォールバック）

外部取得は EOL_REFRESH_ENABLED=true のときだけ行われる（eol_refresh.py 参照）。
Source: https://endoflife.date/
"""
import time
from datetime import date, datetime, timedelta

# {canonical_name: {cycle: eol_date_or_None}}
# None means "not EOL / actively supported"
_EOL: dict[str, dict[str, date | None]] = {
    'redis': {
        '5':   date(2022, 3, 31),
        '6':   date(2024, 3, 31),
        '7':   None,
        '7.0': None,
        '7.2': None,
        '7.4': None,
        '8':   None,
    },
    'php': {
        '7.2': date(2020, 11, 30),
        '7.3': date(2021, 12, 6),
        '7.4': date(2022, 11, 28),
        '8.0': date(2023, 11, 26),
        '8.1': date(2025, 12, 31),
        '8.2': date(2026, 12, 31),
        '8.3': None,
        '8.4': None,
    },
    'nginx': {
        '1.18': date(2021, 4, 20),
        '1.20': date(2023, 5, 23),
        '1.22': None,
        '1.24': None,
        '1.26': None,
        '1.27': None,
    },
    'nodejs': {
        '14': date(2023, 4, 30),
        '16': date(2023, 9, 11),
        '18': date(2025, 4, 30),
        '20': date(2026, 4, 30),
        '21': date(2024, 6, 1),
        '22': None,
        '23': None,
    },
    'python': {
        '3.7': date(2023, 6, 27),
        '3.8': date(2024, 10, 7),
        '3.9': date(2025, 10, 5),
        '3.10': date(2026, 10, 4),
        '3.11': None,
        '3.12': None,
        '3.13': None,
    },
    'mysql': {
        '5.6': date(2021, 2, 28),
        '5.7': date(2023, 10, 31),
        '8.0': date(2026, 4, 30),
        '8.4': None,
    },
    'postgresql': {
        '9.6': date(2021, 11, 11),
        '10':  date(2022, 11, 10),
        '11':  date(2023, 11, 9),
        '12':  date(2024, 11, 14),
        '13':  date(2025, 11, 13),
        '14':  None,
        '15':  None,
        '16':  None,
        '17':  None,
    },
    'ruby': {
        '2.6': date(2022, 3, 31),
        '2.7': date(2023, 3, 31),
        '3.0': date(2024, 3, 31),
        '3.1': date(2025, 3, 31),
        '3.2': None,
        '3.3': None,
    },
    'apache': {
        '2.2': date(2017, 12, 31),
        '2.4': None,
    },
    'memcached': {
        '1.5': date(2020, 12, 31),
        '1.6': None,
    },
    'rabbitmq': {
        '3.9':  date(2023, 7, 31),
        '3.10': date(2023, 12, 31),
        '3.11': date(2024, 9, 30),
        '3.12': None,
        '3.13': None,
    },
    'elasticsearch': {
        '6':  date(2022, 2, 10),
        '7':  date(2024, 8, 31),
        '8':  None,
    },
    'opensearch': {
        '1': date(2025, 6, 30),
        '2': None,
    },
    'go': {
        '1.19': date(2023, 9, 5),
        '1.20': date(2024, 2, 6),
        '1.21': date(2024, 8, 6),
        '1.22': date(2025, 2, 4),
        '1.23': None,
        '1.24': None,
    },
    'java': {
        '8':  None,
        '11': None,
        '17': None,
        '21': None,
        '23': None,
    },
}

# Name aliases → canonical
_ALIASES: dict[str, str] = {
    'php-fpm':      'php',
    'phpfpm':       'php',
    'php fpm':      'php',
    'node':         'nodejs',
    'node.js':      'nodejs',
    'postgres':     'postgresql',
    'pg':           'postgresql',
    'apache httpd': 'apache',
    'httpd':        'apache',
    'gunicorn':     None,  # no formal EOL
    'uvicorn':      None,
    'celery':       None,
    'composer':     None,
}

_WARNING_DAYS = 180  # warn if EOL within 6 months


def _normalize(name: str) -> str:
    return name.lower().strip()


def _major_minor(version: str, parts: int) -> str:
    segs = version.split('.')
    return '.'.join(segs[:parts])


def _parse_date(v) -> date | None:
    """date | 'YYYY-MM-DD' | None -> date | None。解釈不能は None（=サポート中扱い）。"""
    if v is None:
        return None
    if isinstance(v, date):
        return v
    try:
        return datetime.strptime(str(v)[:10], '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None


def canonical(name: str):
    """表示名 -> canonical product 名。None は『EOL 概念なし』、未知は正規化済みの名前を返す。"""
    norm = _normalize(name)
    return _ALIASES.get(norm, norm)


def base_products() -> list[str]:
    """既知のベースプロダクト一覧（_EOL のキー）。固定取得対象としても使う。"""
    return list(_EOL.keys())


# --- 有効データセット（スナップショットを _EOL に重ねる）。プロセス内 TTL キャッシュ ---
_CACHE_TTL_SECONDS = 300
_eff_cache: dict | None = None
_eff_cache_at: float = 0.0


def invalidate_cache() -> None:
    """スナップショット更新後に呼ぶ（次回参照で再構築）。"""
    global _eff_cache
    _eff_cache = None


def _load_snapshot_data() -> dict | None:
    """最新 EolSnapshot の data を返す。DB 未準備でも例外を投げない。"""
    try:
        from .models import EolSnapshot
        snap = EolSnapshot.objects.order_by('-fetched_at').first()
        return snap.data if snap else None
    except Exception:
        return None


def _effective() -> dict:
    """_EOL にスナップショットを重ねた有効データセット（TTL キャッシュ付き）。"""
    global _eff_cache, _eff_cache_at
    now = time.monotonic()
    if _eff_cache is not None and (now - _eff_cache_at) < _CACHE_TTL_SECONDS:
        return _eff_cache

    eff = {k: dict(v) for k, v in _EOL.items()}
    snap = _load_snapshot_data()
    if snap:
        for product, cycles in snap.items():
            if isinstance(cycles, dict):
                eff.setdefault(product, {}).update(cycles)

    _eff_cache, _eff_cache_at = eff, now
    return eff


def get_eol_status(name: str, version: str) -> str:
    """Return 'eol' | 'warning' | 'ok' | 'unknown'."""
    canon = canonical(name)
    if canon is None:
        return 'unknown'
    cycles = _effective().get(canon)
    if not cycles:
        return 'unknown'

    today   = date.today()
    version = version.strip()

    # Try longest match first (major.minor), then major only
    for n_parts in (2, 1):
        cycle = _major_minor(version, n_parts)
        if cycle not in cycles:
            continue
        eol_date = _parse_date(cycles[cycle])
        if eol_date is None:
            return 'ok'
        if eol_date < today:
            return 'eol'
        if eol_date < today + timedelta(days=_WARNING_DAYS):
            return 'warning'
        return 'ok'

    return 'unknown'
