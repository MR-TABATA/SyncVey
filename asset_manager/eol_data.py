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
    # AWS 自身のスケジュール（取得したときだけ入る。値は {'warn': 日付, 'end': 日付} の 2 段）
    'amazon-eks': {},
    'aws-lambda': {},
    'amazon-rds-postgresql': {},
    'amazon-rds-mysql': {},
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


# AWS 自身のスケジュールを持つ product。値は {'warn': 日付|None, 'end': 日付|None} の 2 段:
#   RDS / EKS : warn=標準サポートの終わり（ここから有償の延長サポート）, end=延長サポートの終わり
#   Lambda    : warn=標準サポートの終わり（非推奨になる日）,              end=更新もできなくなる日
# 延長サポートが無い版は warn=None, end=標準サポートの終わり。
AWS_SCHEDULE_PRODUCTS = ('aws-lambda', 'amazon-rds-postgresql', 'amazon-rds-mysql', 'amazon-eks')


def _milestones(value):
    """cycle の値 -> (warn, end)。従来の『日付 1 つ / None』は (None, 日付) として読む。"""
    if isinstance(value, dict):
        return _parse_date(value.get('warn')), _parse_date(value.get('end'))
    return None, _parse_date(value)


def _find_cycle(cycles: dict, version: str):
    """version に合う cycle のキー。完全一致 -> major.minor -> major の順。無ければ None。"""
    version = version.strip()
    if version in cycles:
        return version
    for n_parts in (2, 1):
        cycle = _major_minor(version, n_parts)
        if cycle in cycles:
            return cycle
    return None


def _judge_full(name: str, version: str) -> dict:
    """判定の全体: {'status', 'stage', 'date', 'warn', 'end'}。

    status: 'eol' | 'warning' | 'ok' | 'unknown'（バッジの色と、ダッシュボードの件数はこれ）
    stage : 'standard' | 'nearing' | 'extended'（RDS/EKS: 有償の延長サポート中）
            | 'deprecated'（Lambda: 非推奨）| 'ended' | None（unknown）
    date  : 画面に出す日付。次の節目、または終わった日。無ければ None
    """
    unknown = {'status': 'unknown', 'stage': None, 'date': None, 'warn': None, 'end': None}
    canon = canonical(name)
    if canon is None:
        return unknown
    cycles = _effective().get(canon)
    if not cycles:
        return unknown
    cycle = _find_cycle(cycles, version)
    if cycle is None:
        return unknown

    warn, end = _milestones(cycles[cycle])
    today = date.today()
    out = {'warn': warn, 'end': end}
    if end is not None and end < today:
        return {**out, 'status': 'eol', 'stage': 'ended', 'date': end}
    if warn is not None and warn < today:
        stage = 'deprecated' if canon == 'aws-lambda' else 'extended'
        return {**out, 'status': 'warning', 'stage': stage, 'date': end}
    upcoming = warn or end
    if upcoming is None:
        return {**out, 'status': 'ok', 'stage': 'standard', 'date': None}
    if upcoming < today + timedelta(days=_WARNING_DAYS):
        return {**out, 'status': 'warning', 'stage': 'nearing', 'date': upcoming}
    return {**out, 'status': 'ok', 'stage': 'standard', 'date': upcoming}


def _judge(name: str, version: str):
    """(status, date) — status は 'eol' | 'warning' | 'ok' | 'unknown'。日付が無い/不明なら None。"""
    j = _judge_full(name, version)
    return j['status'], j['date']


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

# 延長サポート（追加料金）／非推奨の説明から飛ぶ AWS 公式ページ。金額は変わるので画面に書かず、ここへ送る。
_AWS_HELP_URLS = {
    'amazon-rds-postgresql': 'https://aws.amazon.com/rds/postgresql/pricing/',
    'amazon-rds-mysql':      'https://aws.amazon.com/rds/mysql/pricing/',
    'amazon-eks':            'https://aws.amazon.com/eks/pricing/',
    'aws-lambda':            'https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtimes.html',
}

# 画面に出す名前。「LAMBDA（サービス）」ではなく「Python 3.7（ミドルウェア）」の期限だと分かるように、
# バッジと詳細にはサービス名でなく、この名前と版を出す。
_PRODUCT_LABELS = {
    'python': 'Python', 'nodejs': 'Node.js', 'ruby': 'Ruby', 'java': 'Java',
    'postgresql': 'PostgreSQL', 'mysql': 'MySQL', 'amazon-eks': 'Kubernetes',
    'amazon-rds-postgresql': 'PostgreSQL', 'amazon-rds-mysql': 'MySQL',
}
# 資産の種別 -> 何の版か（詳細画面の補足）
_ASSET_KIND = {'RDS': 'engine', 'LAMBDA': 'runtime', 'EKS': 'kubernetes'}
# 3 種類の「サポート終了」を見分けるアイコン（lucide）: ランタイム / DB エンジン / Kubernetes
_KIND_ICONS = {'runtime': 'terminal', 'engine': 'database', 'kubernetes': 'ship-wheel'}

# Aurora でない RDS のエンジン -> AWS 自身のスケジュールの product
_AWS_RDS_PRODUCTS = {'postgres': 'amazon-rds-postgresql', 'mysql': 'amazon-rds-mysql'}

_LEADING_VERSION = re.compile(r'^(\d+(?:\.\d+)*)')
_LAMBDA_RUNTIME  = re.compile(r'^([a-z]+)(\d+(?:\.\d+)?)')


def asset_eol_target(asset_type: str, raw: dict | None):
    """資産 -> (product, version)。判定対象外・情報不足なら None。"""
    raw = raw or {}
    kind = (asset_type or '').upper()
    if kind == 'RDS':
        engine = str(raw.get('engine', '')).lower()
        product = _RDS_ENGINES.get(engine)
        m = _LEADING_VERSION.match(str(raw.get('engine_version', '')).strip())
        if not (product and m):
            return None
        # RDS が止める日（取得済みなら）。Aurora は独自の予定なので、元の DB の期限で近似する。
        aws = _AWS_RDS_PRODUCTS.get(engine)
        if aws and _find_cycle(_effective().get(aws) or {}, m.group(1)):
            return (aws, m.group(1))
        return (product, m.group(1))
    if kind == 'LAMBDA':
        runtime = str(raw.get('runtime', '')).strip().lower()
        if runtime in (_effective().get('aws-lambda') or {}):
            return ('aws-lambda', runtime)
        m = _LAMBDA_RUNTIME.match(runtime)
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
    j = _judge_full(*target)
    date_ = j['date']
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
    label, version = _PRODUCT_LABELS.get(product, product), target[1]
    if product == 'aws-lambda':
        # 'python3.12' -> 'Python' + '3.12'。言語名に分けられないもの（provided.al2 など）は名前のまま。
        m = _LAMBDA_RUNTIME.match(target[1])
        lang = _LAMBDA_RUNTIMES.get(m.group(1)) if m else None
        label, version = (_PRODUCT_LABELS.get(lang, lang), m.group(2)) if lang else (target[1], '')
    return {
        'status':  status,
        'stage':   j['stage'],
        'schedule': 'aws' if product in AWS_SCHEDULE_PRODUCTS else 'upstream',
        'help_url': _AWS_HELP_URLS.get(product) if j['stage'] in ('extended', 'deprecated') else None,
        'warn_date': j['warn'],
        'end_date':  j['end'],
        'product': product,
        'label':   label,
        'kind':    kind,
        'icon':    _KIND_ICONS.get(kind, 'circle'),
        'shown':   shown,
        'version': version,
        'date':    date_,
    }
