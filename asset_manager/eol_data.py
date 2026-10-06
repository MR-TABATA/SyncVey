"""
EOL (End of Life) database for common middleware and runtimes.

判定は次の優先順で行う:
  1. DB の最新 EolSnapshot（endoflife.date から取得したデータ）
  2. 下記ハードコード辞書 _EOL（オフライン/未取得時のフォールバック）

外部取得は EOL_REFRESH_ENABLED=true のときだけ行われる（eol_refresh.py 参照）。
Source: https://endoflife.date/
"""
import re
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
    # EKS は Kubernetes のバージョンごとにサポート期限が決まっている。日付は内蔵せず、
    # endoflife.date から取得したスナップショットだけで判定する（未取得なら 'unknown'）。
    'amazon-eks': {},
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


def _judge(name: str, version: str):
    """(status, eol_date) — status は 'eol' | 'warning' | 'ok' | 'unknown'。日付が無い/不明なら None。"""
    canon = canonical(name)
    if canon is None:
        return 'unknown', None
    cycles = _effective().get(canon)
    if not cycles:
        return 'unknown', None

    today   = date.today()
    version = version.strip()

    # Try longest match first (major.minor), then major only
    for n_parts in (2, 1):
        cycle = _major_minor(version, n_parts)
        if cycle not in cycles:
            continue
        eol_date = _parse_date(cycles[cycle])
        if eol_date is None:
            return 'ok', None
        if eol_date < today:
            return 'eol', eol_date
        if eol_date < today + timedelta(days=_WARNING_DAYS):
            return 'warning', eol_date
        return 'ok', eol_date

    return 'unknown', None


def get_eol_status(name: str, version: str) -> str:
    """Return 'eol' | 'warning' | 'ok' | 'unknown'."""
    return _judge(name, version)[0]


# --- 資産（RDS / Lambda / EKS）の判定 ------------------------------------------------------
#
# AppDependency（アプリが使うミドルウェア・言語）だけでなく、クラウド資産そのものにも
# サポート期限がある: RDS のエンジン、Lambda のランタイム、EKS の Kubernetes バージョン。
# 資産の raw_data から (product, version) を取り出し、get_eol_status に渡す。
# 判定できない（未対応のエンジン・ランタイム、バージョン欠落）ものは 'unknown'。

# RDS の Engine -> canonical product。Aurora は同じエンジンの upstream 期限で近似する
# （Aurora 独自のサポート終了日とは一致しないことがある）。
_RDS_ENGINES = {
    'postgres':          'postgresql',
    'aurora-postgresql': 'postgresql',
    'mysql':             'mysql',
    'aurora-mysql':      'mysql',
}

# Lambda の Runtime 接頭辞 -> canonical product。dotnet / go1.x / provided.* は
# _EOL に無いので対象外（unknown）。Lambda 側の廃止日は言語の EOL と近いが同一ではない。
_LAMBDA_RUNTIMES = {
    'python': 'python',
    'nodejs': 'nodejs',
    'ruby':   'ruby',
    'java':   'java',
}

# 画面に出す名前。「LAMBDA（サービス）」ではなく「Python 3.7（ミドルウェア）」の期限だと分かるように、
# バッジと詳細にはサービス名でなく、この名前と版を出す。
_PRODUCT_LABELS = {
    'python': 'Python', 'nodejs': 'Node.js', 'ruby': 'Ruby', 'java': 'Java',
    'postgresql': 'PostgreSQL', 'mysql': 'MySQL', 'amazon-eks': 'Kubernetes',
}
# 資産の種別 -> 何の版か（詳細画面の補足）
_ASSET_KIND = {'RDS': 'engine', 'LAMBDA': 'runtime', 'EKS': 'kubernetes'}
# 3 種類の「サポート終了」を見分けるアイコン（lucide）: ランタイム / DB エンジン / Kubernetes
_KIND_ICONS = {'runtime': 'terminal', 'engine': 'database', 'kubernetes': 'ship-wheel'}

_LEADING_VERSION = re.compile(r'^(\d+(?:\.\d+)*)')
_LAMBDA_RUNTIME  = re.compile(r'^([a-z]+)(\d+(?:\.\d+)?)')


def asset_eol_target(asset_type: str, raw: dict | None):
    """資産 -> (product, version)。判定対象外・情報不足なら None。"""
    raw = raw or {}
    kind = (asset_type or '').upper()
    if kind == 'RDS':
        product = _RDS_ENGINES.get(str(raw.get('engine', '')).lower())
        m = _LEADING_VERSION.match(str(raw.get('engine_version', '')).strip())
        return (product, m.group(1)) if product and m else None
    if kind == 'LAMBDA':
        m = _LAMBDA_RUNTIME.match(str(raw.get('runtime', '')).lower())
        product = _LAMBDA_RUNTIMES.get(m.group(1)) if m else None
        return (product, m.group(2)) if product else None
    if kind == 'EKS':
        m = _LEADING_VERSION.match(str(raw.get('version', '')).strip())
        return ('amazon-eks', m.group(1)) if m else None
    return None


def get_asset_eol_status(asset_type: str, raw: dict | None) -> str:
    """資産の EOL 状態: 'eol' | 'warning' | 'ok' | 'unknown'。"""
    target = asset_eol_target(asset_type, raw)
    if target is None:
        return 'unknown'
    return get_eol_status(*target)


def _version_key(v: str):
    return tuple(int(x) for x in re.findall(r'\d+', v))


def eks_supported_floor():
    """いま EKS の標準サポート中の、いちばん古い Kubernetes バージョン（'1.31' など）。

    取得済みの amazon-eks のスケジュールから計算する。データが無い・全部期限切れなら None。
    「Kubernetes 1.24 はサポート終了」とだけ言わず、「EKS は 1.31 以上をサポート」と、行き先を
    示すために使う。
    """
    cycles = _effective().get('amazon-eks') or {}
    today = date.today()
    alive = []
    for cycle, eol in cycles.items():
        d = _parse_date(eol)
        if (d is None or d >= today) and _version_key(cycle):
            alive.append(cycle)
    return min(alive, key=_version_key) if alive else None


def asset_eol_info(asset_type: str, raw: dict | None):
    """資産の詳細画面用: {'status', 'product', 'version', 'date'}。判定対象外・不明なら None。

    `date` はそのバージョン（サイクル）のサポート終了日。日付が無い（サポート中で未定）なら None。
    """
    target = asset_eol_target(asset_type, raw)
    if target is None:
        return None
    # get_eol_status を経由する（テストや呼び出し側が差し替えられるよう、判定の入口は 1 つに保つ）
    status = get_eol_status(*target)
    if status == 'unknown':
        return None
    date_ = _judge(*target)[1]
    product = target[0]
    kind = _ASSET_KIND.get((asset_type or '').upper(), '')
    raw = raw or {}
    # 一覧の「中身の行」に出す文字列: 保存されている値そのまま（aurora-mysql など、判定に使った
    # 正規化前の名前を失わない）。
    if kind == 'engine':
        shown = f"{raw.get('engine', '')} {raw.get('engine_version', '')}".strip()
    elif kind == 'runtime':
        shown = str(raw.get('runtime', ''))
    else:
        shown = f"Kubernetes {target[1]}"
    return {
        'status':  status,
        'product': product,
        'label':   _PRODUCT_LABELS.get(product, product),
        'kind':    kind,
        'icon':    _KIND_ICONS.get(kind, 'circle'),
        'shown':   shown,
        'floor':   eks_supported_floor() if kind == 'kubernetes' else None,
        'version': target[1],
        'date':    date_,
    }
