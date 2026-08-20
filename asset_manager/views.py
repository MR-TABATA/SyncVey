import json
import logging
import os
from functools import wraps

from django.conf import settings as _settings
from django.shortcuts import render, get_object_or_404, redirect
from django.db.models import Q, Count, Case, When, IntegerField
from django.db.utils import OperationalError, ProgrammingError
from django.utils.text import slugify
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.utils.translation import gettext as _, gettext_lazy
from django.http import HttpResponseBadRequest, HttpResponse, Http404
from django.views.decorators.http import require_POST, require_GET
from django.db import transaction
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth import get_user_model
from django.http import HttpResponseForbidden
from django.utils import timezone

from .models import (
    Asset, System, Environment,
    Application, AppEnvConfig, AppDependency,
    Organization, Membership, UserProfile, AuditLog,
)
from .resource_registry import (
    PRIMARY_ASSET_TYPES,
    ICON_MAP,
    PROVIDER_LABELS,
    CATEGORY_LABELS,
    resolve_resource_type,
    resolve_provider,
    get_known_asset_types,
    get_provider_choices,
)

_User = get_user_model()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Drift detection helpers
# ---------------------------------------------------------------------------

# raw_data のうち diff 表示から除外するキー
# （Terraform 内部フィールド・AWS 側が自動更新する動的パラメータ）
_DIFF_EXCLUDE = frozenset({
    # Terraform 内部
    '_resource_type', '_scan_source', 'tags_all', 'timeouts', 'tags',
    # 識別子（変わらない）
    'arn', 'id',
    # ECS — AWS が自動更新する動的値
    'running_count', 'pending_count',
    'deployment_maximum_percent', 'deployment_minimum_healthy_percent',
    'deployments',
    # RDS — 自動バックアップ・メンテナンス系
    'latest_restorable_time', 'status',
    'pending_modified_values', 'read_replica_db_instance_identifiers',
    # EC2 — 起動・停止で変わる値
    'launch_time', 'state_transition_reason',
    'state_reason',
    # 汎用タイムスタンプ
    'last_modified', 'last_modified_time', 'created_time',
    'created_at', 'updated_at',
    # DynamoDB / ElastiCache / EFS / Route53 — AWS が自動更新する動的値
    'item_count', 'table_status', 'cache_cluster_status',
    'lifecycle_state', 'number_of_mount_targets', 'record_count',
    'size_in_bytes',
})

# tfstate / Boto3 レスポンスから除去するシークレット系キー名（部分一致）
_SECRET_KEY_PATTERNS = (
    'password', 'secret', 'token', 'private_key',
    'access_key', 'credential', 'auth',
)

_SECRET_PLACEHOLDER = '***'  # nosec B105 - シークレット値の表示用マスク文字列（実パスワードではない）


def _detect_secrets(tfstate_data: dict) -> dict:
    """
    tfstate 内のシークレット系フィールドを検出する。
    Returns: {field_name: 件数} — 空なら機密情報なし
    """
    found: dict[str, int] = {}
    for resource in tfstate_data.get('resources', []):
        if resource.get('mode') == 'managed' and resource.get('instances'):
            attrs = resource['instances'][0].get('attributes') or {}
            for k, v in attrs.items():
                if not v or v == _SECRET_PLACEHOLDER:
                    continue
                lower_k = k.lower()
                if any(pat in lower_k for pat in _SECRET_KEY_PATTERNS):
                    found[k] = found.get(k, 0) + 1
    return found


def _scrub_secrets(attrs: dict) -> dict:
    """
    attributes dict からシークレット系の値を除去して返す。
    キー名に _SECRET_KEY_PATTERNS のいずれかが含まれる場合に置換する。
    """
    scrubbed = {}
    for k, v in attrs.items():
        lower_k = k.lower()
        if any(pat in lower_k for pat in _SECRET_KEY_PATTERNS):
            scrubbed[k] = _SECRET_PLACEHOLDER
        else:
            scrubbed[k] = v
    return scrubbed


def _compute_raw_diff(old: dict, new: dict) -> list:
    """
    old / new の raw_data を比較し、変更フィールドのリストを返す。
    [{'field': str, 'old': str, 'new': str}, ...]

    キーは「両方に存在するもの」の積集合(&)だけを比較する。
    tfstate インポートは全属性(50+キー)を保存する一方、ライブスキャン
    (scanner.py)は厳選した互換キー(~11)のみを出力するため、和集合(|)で
    比較すると scanner が出さない tfstate 固有キーが全て「削除」と誤検知
    される。実ドリフトは共通キー上で起きるので積集合で比較するのが正しい。
    新規/削除リソースの検出は raw_data_prev の有無で別途行う(drift_report_view)。
    """
    changes = []
    keys = (set(old.keys()) & set(new.keys())) - _DIFF_EXCLUDE
    for key in sorted(keys):
        ov = old.get(key)
        nv = new.get(key)
        if ov != nv:
            changes.append({
                'field': key,
                'old': str(ov) if ov is not None else '',
                'new': str(nv) if nv is not None else '',
            })
    return changes


def _get_env_drift_summary(environment) -> dict:
    """
    環境カードのバッジ用。
    {'changed': N, 'added': N, 'total': N, 'has_data': bool}
    """
    from .autoscaling import is_autoscaling_churn

    assets = environment.assets.only(
        'raw_data', 'raw_data_prev', 'last_imported_at', 'missing_since',
    )
    changed = added = removed = autoscaling = 0
    has_data = False
    for asset in assets:
        if asset.last_imported_at:
            has_data = True
        if asset.missing_since:
            # AWS から消えた。ASG のスケールインなら churn（added の抑制と
            # 対称）、それ以外は本物の removed ドリフト。
            if is_autoscaling_churn(asset.raw_data):
                autoscaling += 1
            else:
                removed += 1
        elif not asset.raw_data_prev:
            # An ASG-owned first-sighting is churn, not an add (cry-wolf fix).
            if is_autoscaling_churn(asset.raw_data):
                autoscaling += 1
            else:
                added += 1
        elif _compute_raw_diff(asset.raw_data_prev, asset.raw_data):
            # 生の != ではなく drift レポートと同じ判定にする。
            # スキーマ非対称(tfstate全属性 vs scan厳選)で raw_data != prev が
            # 常に真になり、バッジ件数が膨らむのを防ぐ。
            changed += 1
    return {'changed': changed, 'added': added, 'removed': removed,
            'autoscaling': autoscaling,
            'total': changed + added + removed, 'has_data': has_data}


def _record_drift_snapshot(environment, source):
    """
    現時点の raw_data / raw_data_prev からドリフトを計算し DriftSnapshot を1件保存する。
    スキャン／取込の直後に呼ぶ前提（その回で持ち込まれた差分を切り取る）。
    資産が無い環境では何もしない。差分ゼロでも推移を残すため記録する。
    """
    from .models import DriftSnapshot
    from .autoscaling import is_autoscaling_churn

    assets = environment.assets.only(
        'asset_type', 'name', 'cloud_id', 'provider', 'raw_data', 'raw_data_prev',
        'missing_since',
    ).order_by('asset_type', 'name')

    changed, added, removed, autoscaling, unchanged = [], [], [], [], 0
    for asset in assets:
        meta = {
            'type':     asset.asset_type,
            'name':     asset.name,
            'cloud_id': asset.cloud_id,
            'provider': asset.provider,
        }
        if asset.missing_since:
            # AWS から消えたリソース。ASG のスケールインは churn として
            # 別枠に逃がす（スケールアウトを added から外したのと対称）。
            if is_autoscaling_churn(asset.raw_data):
                autoscaling.append(meta)
            else:
                removed.append(meta)
        elif not asset.raw_data_prev:
            # ASG-owned first-sighting = autoscaling churn, kept out of the drift
            # counts but recorded so the history is honest about what happened.
            if is_autoscaling_churn(asset.raw_data):
                autoscaling.append(meta)
            else:
                added.append(meta)
        else:
            diff = _compute_raw_diff(asset.raw_data_prev, asset.raw_data)
            if diff:
                changed.append({**meta, 'changes': diff})
            else:
                unchanged += 1

    if not (changed or added or removed or autoscaling or unchanged):
        return None

    snapshot = DriftSnapshot.objects.create(
        environment=environment,
        source=source,
        changed_count=len(changed),
        added_count=len(added),
        removed_count=len(removed),
        unchanged_count=unchanged,
        detail={'changed': changed, 'added': added, 'removed': removed,
                'autoscaling': autoscaling},
    )
    # 差分ゼロでも毎回1行積むため、env ごとに上限を超えた古い分を間引く
    DriftSnapshot.prune(environment)
    return snapshot


def _safe_query_or_empty(query_fn):
    try:
        return query_fn()
    except (ProgrammingError, OperationalError):
        return []


def htmx_login_required(view_fn):
    @wraps(view_fn)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            if request.headers.get('HX-Request'):
                response = HttpResponse(status=204)
                response['HX-Redirect'] = '/login/'
                return response
            return redirect('/login/')
        return view_fn(request, *args, **kwargs)
    return wrapper


def _get_user_org(request):
    if not request.user.is_authenticated:
        return None
    membership = Membership.objects.filter(user=request.user).select_related('organization').first()
    return membership.organization if membership else None


# --- 組織スコープ付きの単一オブジェクト取得（IDOR 対策）------------------------
# 呼び出し側ユーザーの組織に属するオブジェクトのみ取得。組織が無い／他組織なら 404。
def _user_system_or_404(request, system_id):
    org = _get_user_org(request)
    if org is None:
        raise Http404
    return get_object_or_404(System, pk=system_id, organization=org)


def _user_environment_or_404(request, environment_id):
    org = _get_user_org(request)
    if org is None:
        raise Http404
    return get_object_or_404(Environment, pk=environment_id, system__organization=org)


def _user_asset_or_404(request, asset_id):
    org = _get_user_org(request)
    if org is None:
        raise Http404
    return get_object_or_404(
        Asset.objects.select_related('environment', 'environment__system'),
        pk=asset_id, environment__system__organization=org,
    )


def _user_environments(request):
    """呼び出しユーザーの組織に属する Environment のクエリセット（組織無しなら空）。"""
    org = _get_user_org(request)
    if org is None:
        return Environment.objects.none()
    return Environment.objects.filter(system__organization=org)


def _get_sidebar_counts(org=None):
    try:
        qs = Asset.objects.filter(asset_type__in=PRIMARY_ASSET_TYPES)
        if org:
            qs = qs.filter(environment__system__organization=org)
        else:
            qs = qs.none()  # 組織未所属は他組織の資産を集計しない（クロステナント漏洩防止）
        rows = qs.values('asset_type').annotate(c=Count('id'))
        by_type = {r['asset_type']: r['c'] for r in rows}
        by_type['ALL'] = sum(by_type.values())
        return by_type
    except (ProgrammingError, OperationalError):
        return {}


# カテゴリ → (lucide アイコン名, Tailwind bg色, Tailwind text色) の表示設定
_CATEGORY_DISPLAY = {
    'COMPUTE':   ('cpu',        'bg-indigo-500',  'text-white'),
    'STORAGE':   ('hard-drive', 'bg-amber-500',   'text-white'),
    'NETWORK':   ('network',    'bg-blue-500',    'text-white'),
    'DATABASE':  ('database',   'bg-emerald-500', 'text-white'),
    'ANALYTICS': ('bar-chart-2','bg-violet-500',  'text-white'),
    'AI_ML':     ('brain',      'bg-pink-500',    'text-white'),
    'SECURITY':  ('shield',     'bg-red-500',     'text-white'),
    'OTHER':     ('box',        'bg-slate-500',   'text-white'),
}


def _get_dashboard_stats(org=None):
    try:
        qs = Asset.objects.all()
        if org:
            qs = qs.filter(environment__system__organization=org)
        else:
            qs = qs.none()  # 組織未所属は他組織の資産を集計しない（クロステナント漏洩防止）
        by_type     = {r['asset_type']:     r['c'] for r in qs.values('asset_type').annotate(c=Count('id'))}
        by_category = {r['asset_category']: r['c'] for r in qs.values('asset_category').annotate(c=Count('id'))}
        by_provider = {r['provider']: r['c'] for r in qs.values('provider').annotate(c=Count('id')) if r['provider']}
        # 資産が1件以上あるシステム・環境のみカウント（表示と一致させる）
        sys_qs = System.objects.filter(organization=org) if org else System.objects.none()
        sys_qs = sys_qs.annotate(_ac=Count('environments__assets', distinct=True)).filter(_ac__gt=0)
        env_qs = Environment.objects.filter(system__organization=org) if org else Environment.objects.none()
        env_qs = env_qs.annotate(_ac=Count('assets')).filter(_ac__gt=0)
        # カテゴリカード: カテゴリ定義順で並べ、件数0は除外
        category_cards = []
        for cat, label in CATEGORY_LABELS.items():
            cnt = by_category.get(cat, 0)
            if cnt == 0:
                continue
            icon, bg, fg = _CATEGORY_DISPLAY.get(cat, ('box', 'bg-slate-500', 'text-white'))
            category_cards.append({'key': cat, 'label': label, 'count': cnt, 'icon': icon, 'bg': bg, 'fg': fg})
        # プロバイダー行: PROVIDER_LABELS の定義順で並べる
        provider_rows = [
            {'key': p, 'label': PROVIDER_LABELS[p], 'count': by_provider[p]}
            for p in PROVIDER_LABELS
            if p in by_provider
        ]
        return {
            'total_assets':   sum(by_type.values()),
            'total_systems':  sys_qs.count(),
            'total_envs':     env_qs.count(),
            'by_type':        by_type,
            'by_category':    by_category,
            'category_cards': category_cards,
            'by_provider':    by_provider,
            'provider_rows':  provider_rows,
        }
    except (ProgrammingError, OperationalError):
        return {'total_assets': 0, 'total_systems': 0, 'total_envs': 0, 'by_type': {}, 'by_category': {}, 'category_cards': [], 'by_provider': {}, 'provider_rows': []}


# 空状態（org 未所属 / DB 未準備）でも壊さないデフォルト
_EMPTY_SIGNALS = {
    'drift_current': 0, 'drift_delta': None, 'drift_top_env_id': None,
    'eol_overdue': 0, 'eol_soon': 0,
    'last_scan': None, 'last_scan_stale': True, 'scanned_systems': 0, 'has_history': False,
}


def _get_dashboard_signals(org=None):
    """
    ヒーロー行用の「シグナル」を返す。

    _get_dashboard_stats が「いま何があるか」(生カウント)なのに対し、こちらは
    「それが緊急か」を判断するためのコンテキスト層:
      - drift_current : 各環境の最新スナップショットの総ドリフト件数の合計
      - drift_delta   : 直前スナップショットとの差分（前回比。履歴のある環境のみ）
      - eol_overdue/soon : サポート終了済み / 期限間近の依存パッケージ数
      - last_scan     : 直近で完了したスキャンの時刻（鮮度表示用）
    生データではなく「次のアクションを選べる」材料にするのが狙い。
    """
    if not org:
        return dict(_EMPTY_SIGNALS)
    try:
        from .models import DriftSnapshot, ScanJob
        from .eol_data import get_eol_status

        # ── Drift: 環境ごとに最新2件を取り、現在値と前回比を出す（1クエリ） ──
        rows = (
            DriftSnapshot.objects
            .filter(environment__system__organization=org)
            .order_by('environment_id', '-detected_at')
            .values('environment_id', 'changed_count', 'added_count', 'removed_count')
        )
        latest_by_env = {}   # env_id -> total_count（最新）
        prev_by_env   = {}   # env_id -> total_count（2番目に新しい）
        for r in rows:
            env_id = r['environment_id']
            # DriftSnapshot.total_count と同じ内訳にすること。ここは .values()
            # で回すためプロパティを使えず、追従漏れが起きやすい。
            total  = r['changed_count'] + r['added_count'] + r['removed_count']
            if env_id not in latest_by_env:
                latest_by_env[env_id] = total
            elif env_id not in prev_by_env:
                prev_by_env[env_id] = total

        drift_current = sum(latest_by_env.values())
        # 前回比は「直前スナップショットがある環境」だけで比較する
        has_history = bool(prev_by_env)
        drift_delta = None
        if has_history:
            cur_for_cmp  = sum(latest_by_env[e] for e in prev_by_env)
            prev_for_cmp = sum(prev_by_env.values())
            drift_delta  = cur_for_cmp - prev_for_cmp
        drift_top_env_id = (
            max(latest_by_env, key=latest_by_env.get)
            if latest_by_env and drift_current > 0 else None
        )

        # ── EOL: 追跡中の依存のうち終了済み / 期限間近を数える ──
        eol_overdue = eol_soon = 0
        deps = (
            AppDependency.objects
            .filter(app_env_config__application__system__organization=org)
            .values_list('name', 'version')
        )
        for name, version in deps:
            status = get_eol_status(name, version)
            if status == 'eol':
                eol_overdue += 1
            elif status == 'warning':
                eol_soon += 1

        # ── Freshness: 直近で完了したスキャン時刻 ──
        last_scan = (
            ScanJob.objects
            .filter(system__organization=org, status=ScanJob.Status.DONE,
                    finished_at__isnull=False)
            .order_by('-finished_at')
            .values_list('finished_at', flat=True)
            .first()
        )
        scanned_systems = (
            ScanJob.objects
            .filter(system__organization=org, status=ScanJob.Status.DONE)
            .values('system_id').distinct().count()
        )
        # 24時間より古い（または未スキャン）なら「鮮度が落ちている」扱い
        last_scan_stale = (
            last_scan is None
            or (timezone.now() - last_scan).total_seconds() > 86400
        )

        return {
            'drift_current':    drift_current,
            'drift_delta':      drift_delta,
            'drift_top_env_id': drift_top_env_id,
            'eol_overdue':      eol_overdue,
            'eol_soon':         eol_soon,
            'last_scan':        last_scan,
            'last_scan_stale':  last_scan_stale,
            'scanned_systems':  scanned_systems,
            'has_history':      has_history,
        }
    except (ProgrammingError, OperationalError):
        return dict(_EMPTY_SIGNALS)


def _systems_queryset(org=None):
    qs = System.objects.filter(organization=org) if org else System.objects.none()
    return (
        qs.annotate(asset_count=Count('environments__assets', distinct=True))
        .filter(asset_count__gt=0)          # 資産が1件以上あるシステムだけ表示
        .prefetch_related('environments')
    )


def _system_list_context(org=None):
    systems = _safe_query_or_empty(lambda: _systems_queryset(org))
    _attach_system_providers(systems)
    return {
        'systems': systems,
        'stats': _get_dashboard_stats(org),
    }


@htmx_login_required
def dashboard_view(request):
    org = _get_user_org(request)
    systems = _safe_query_or_empty(lambda: _systems_queryset(org))
    _attach_system_providers(systems)
    try:
        app_qs = Application.objects.filter(system__organization=org) if org else Application.objects.none()
        apps_count = app_qs.count()
    except (ProgrammingError, OperationalError):
        apps_count = 0
    try:
        audit_count = AuditLog.objects.count()
    except (ProgrammingError, OperationalError):
        audit_count = 0
    context = {
        'systems': systems,
        'env_types': Environment.EnvType.choices,
        'stats': _get_dashboard_stats(org),
        'signals': _get_dashboard_signals(org),
        'user_org': org,
        'apps_count': apps_count,
        'audit_count': audit_count,
    }
    return render(request, 'dashboard.html', context)


@require_GET
@htmx_login_required
def system_list_view(request):
    org = _get_user_org(request)
    return render(request, '_system_list.html', _system_list_context(org))


# ---------------------------------------------------------------------------
# アイコンヘルパー（resource_registry.ICON_MAP 参照）
# ---------------------------------------------------------------------------

# 全プロバイダー分の (asset_type → icon_path) を平坦化
# provider ごとに ICON_MAP を合成し、同 asset_type が複数あれば最初に登録したものを採用
_ASSET_TYPE_ICONS: dict[str, str] = {}
for _provider_icons in ICON_MAP.values():
    for _atype, _icon in _provider_icons.items():
        _ASSET_TYPE_ICONS.setdefault(_atype, _icon)


def _icon_url(asset):
    """アセットの static 相対アイコンパスを返す。未定義なら None。"""
    return ICON_MAP.get(asset.provider, {}).get(asset.asset_type)


# ---------------------------------------------------------------------------
# プロバイダーバッジ
# ---------------------------------------------------------------------------

_PROVIDER_BADGE_COLORS: dict[str, str] = {
    'AWS': 'bg-orange-100 text-orange-700',
}


def _provider_badge(provider: str) -> dict:
    """プロバイダーコード → {key, label, color} dict を返す。"""
    label = PROVIDER_LABELS.get(provider, provider)
    color = _PROVIDER_BADGE_COLORS.get(provider, 'bg-slate-100 text-slate-600')
    return {'key': provider, 'label': label, 'color': color}


def _attach_system_providers(systems) -> None:
    """systems クエリセット / リストの各オブジェクトに .providers を付与する。"""
    if not systems:
        return
    provider_rows = (
        Asset.objects.filter(environment__system__in=systems)
        .values('environment__system_id', 'provider')
        .distinct()
    )
    sys_providers: dict[int, list] = {}
    for row in provider_rows:
        sid = row['environment__system_id']
        p   = row['provider']
        if p:
            seen = {b['key'] for b in sys_providers.get(sid, [])}
            if p not in seen:
                sys_providers.setdefault(sid, []).append(_provider_badge(p))
    for system in systems:
        system.providers = sys_providers.get(system.id, [])


def _attach_env_providers(environments) -> None:
    """environments リストの各オブジェクトに .providers を付与する。"""
    if not environments:
        return
    provider_rows = (
        Asset.objects.filter(environment__in=environments)
        .values('environment_id', 'provider')
        .distinct()
    )
    env_providers: dict[int, list] = {}
    for row in provider_rows:
        eid = row['environment_id']
        p   = row['provider']
        if p:
            seen = {b['key'] for b in env_providers.get(eid, [])}
            if p not in seen:
                env_providers.setdefault(eid, []).append(_provider_badge(p))
    for env in environments:
        env.providers = env_providers.get(env.id, [])


@require_GET
@htmx_login_required
def environment_list_view(request, system_id):
    try:
        system = _user_system_or_404(request, system_id)
        # PROD → STG → DEV → OTHER の順で表示、資産が1件以上の環境のみ
        environments = list(
            Environment.objects.filter(system=system).annotate(
                env_order=Case(
                    When(env_type='PROD', then=0),
                    When(env_type='STG',  then=1),
                    When(env_type='DEV',  then=2),
                    default=3,
                    output_field=IntegerField(),
                ),
                total_assets=Count('assets'),
            ).filter(total_assets__gt=0).order_by('env_order', 'name')
        )
        rows = Asset.objects.filter(environment__system=system).values('environment_id', 'asset_type').annotate(c=Count('id'))
        counts_by_env = {}
        for r in rows:
            counts_by_env.setdefault(r['environment_id'], {})[r['asset_type']] = r['c']
        for env in environments:
            env_counts = counts_by_env.get(env.id, {})
            # アイコンがある asset_type のみ表示（補助リソースは除外）
            chips = []
            for atype, count in env_counts.items():
                icon_url = _ASSET_TYPE_ICONS.get(atype, '')
                if not icon_url:
                    continue  # アイコンなし＝補助リソースは非表示
                chips.append({'icon_url': icon_url, 'label': atype, 'count': count})
            # カウント降順でソート
            env.asset_type_chips = sorted(chips, key=lambda c: -c['count'])
        # 各環境にプロバイダーバッジを付与
        _attach_env_providers(environments)
        # 各環境に drift サマリーを付与
        for env in environments:
            env.drift_summary = _get_env_drift_summary(env)
    except (ProgrammingError, OperationalError):
        return render(request, '_environment_list.html', {'system': None, 'environments': []})
    return render(request, '_environment_list.html', {'system': system, 'environments': environments})


@htmx_login_required
def asset_list_view(request):
    search_query      = request.GET.get('q', '')
    provider_filter   = request.GET.get('provider', '')
    asset_type_filter = request.GET.get('asset_type', '')
    system_id         = request.GET.get('system_id')
    environment_id    = request.GET.get('environment_id')
    # AWS から消えた資産は既定で伏せる（台帳を「今あるもの」に保つ）。
    # ?show_missing=1 で消えた分も並べて出せる。行自体は消していない。
    show_missing      = request.GET.get('show_missing') == '1'

    try:
        assets = Asset.objects.select_related('environment', 'environment__system').all()
    except (ProgrammingError, OperationalError):
        return render(request, '_asset_list.html', {
            'assets': [], 'selected_system': None, 'selected_environment': None,
            'message': _('DB tables not created. Run `python manage.py migrate`.'),
        })

    org = _get_user_org(request)
    assets = assets.filter(environment__system__organization=org) if org else assets.none()

    selected_system = selected_environment = None

    if system_id:
        selected_system = _user_system_or_404(request, system_id)
        assets = assets.filter(environment__system_id=selected_system.id)

    if environment_id:
        selected_environment = _user_environment_or_404(request, environment_id)
        assets = assets.filter(environment_id=selected_environment.id)
        if not selected_system:
            selected_system = selected_environment.system

    if search_query:
        assets = assets.filter(Q(name__icontains=search_query) | Q(cloud_id__icontains=search_query))
    if provider_filter:
        assets = assets.filter(provider=provider_filter)
    if asset_type_filter and asset_type_filter != 'ALL':
        assets = assets.filter(asset_type=asset_type_filter)

    # 伏せる前に件数を数えて「N 件が消えています」と出せるようにする。
    # 黙って減らすと台帳が壊れたように見えるため。
    missing_count = assets.filter(missing_since__isnull=False).count()
    if not show_missing:
        assets = assets.filter(missing_since__isnull=True)

    # 表示トグル用のリンク先。show_missing を落とした素のクエリを作り、
    # テンプレート側で付け外しする（テンプレートで GET を編集できないため）。
    base_params = request.GET.copy()
    base_params.pop('show_missing', None)
    toggle_query = base_params.urlencode()

    return render(request, '_asset_list.html', {
        'assets':               assets.order_by('-updated_at'),
        'selected_system':      selected_system,
        'selected_environment': selected_environment,
        'provider_choices':     get_provider_choices(),
        'active_asset_type':    asset_type_filter,
        'missing_count':        missing_count,
        'show_missing':         show_missing,
        'toggle_query':         toggle_query,
    })


# ---------------------------------------------------------------------------
# tfstate 処理（resource_registry 経由・全プロバイダー対応）
# ---------------------------------------------------------------------------

def _get_or_create_system_and_environment(tfstate_config, org=None):
    system_name      = tfstate_config.get('system', 'Unknown System')
    env_name         = tfstate_config.get('env', 'Unknown Environment')
    tfstate_filename = tfstate_config.get('file')

    requested_code = tfstate_config.get('code')
    default_code   = slugify(system_name)[:50] or 'system'
    system_code    = (requested_code or default_code)[:50]

    qs = System.objects.filter(name=system_name)
    if org:
        qs = qs.filter(organization=org)
    system = qs.first()
    if system is None:
        candidate = system_code
        suffix = 2
        while System.objects.filter(code=candidate).exists():
            base = system_code[:max(1, 50 - len(str(suffix)) - 1)]
            candidate = f"{base}-{suffix}"
            suffix += 1
        system = System.objects.create(name=system_name, code=candidate, organization=org)

    env_type = tfstate_config.get('env_type') or (
        Environment.EnvType.PROD if env_name.upper() == 'PROD' else Environment.EnvType.DEV
    )
    environment, _ = Environment.objects.get_or_create(
        system=system, name=env_name,
        defaults={'tfstate_filename': tfstate_filename, 'env_type': env_type}
    )
    if tfstate_filename:
        environment.tfstate_filename = tfstate_filename
        environment.save(update_fields=['tfstate_filename'])

    return system, environment


def _extract_tfstate_config(tfstate_data, filename):
    outputs = tfstate_data.get('outputs') or {}

    def output_value(*keys):
        for key in keys:
            value = outputs.get(key)
            if isinstance(value, dict):
                value = value.get('value')
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    system_name = output_value('system', 'system_name', 'project', 'project_name', 'service')
    env_name    = output_value('env', 'environment', 'environment_name', 'stage', 'workspace')
    region      = output_value('region', 'aws_region')

    if not system_name or not env_name:
        for resource in tfstate_data.get('resources', []):
            if resource.get('mode') != 'managed' or not resource.get('instances'):
                continue
            attributes = resource['instances'][0].get('attributes') or {}
            tags = attributes.get('tags') or {}
            if not system_name:
                system_name = tags.get('System') or tags.get('system') or tags.get('Project') or tags.get('project')
            if not env_name:
                env_name = tags.get('Environment') or tags.get('environment') or tags.get('Env') or tags.get('env')
            if not region:
                region = attributes.get('region')
            if system_name and env_name:
                break

    stem = os.path.splitext(filename)[0]
    if not system_name or not env_name:
        parts = stem.split('-')
        if len(parts) >= 2:
            env_candidate = parts[-1].upper()
            if env_candidate in dict(Environment.EnvType.choices):
                env_name    = env_name    or env_candidate
                system_name = system_name or '-'.join(parts[:-1])

    if not system_name or not env_name:
        return None

    env_upper = env_name.upper()
    env_type  = env_upper if env_upper in dict(Environment.EnvType.choices) else Environment.EnvType.DEV

    return {
        'system': system_name,
        'code':   slugify(system_name)[:50],
        'env':    env_name,
        'env_type': env_type,
        'region': region,
        'file':   filename,
    }



def _create_or_update_asset(resource_data, environment):
    """
    resource_registry 経由で全プロバイダー・全サービスに対応。
    Detail テーブルは廃止済み — raw_data のみ使用。
    """
    resource_type = resource_data['type']
    resource_name = resource_data['name']
    attributes    = _scrub_secrets(dict(resource_data['instances'][0]['attributes']))
    attributes['_resource_type'] = resource_type

    provider                     = resolve_provider(resource_type)
    asset_type, asset_category   = resolve_resource_type(resource_type, attributes)

    cloud_id = attributes.get('id') or attributes.get('arn')
    if not cloud_id:
        cloud_id = f"{resource_type}-{resource_name}-{environment.system.code}-{environment.name}"

    asset_name = (
        attributes.get('name') or attributes.get('bucket') or attributes.get('cluster_identifier')
        or attributes.get('dns_name') or attributes.get('identifier') or resource_name
    )
    region = (
        attributes.get('region')
        or (environment.system.aws_scan_regions[0]
            if getattr(environment.system, 'aws_scan_regions', None) else None)
    )

    asset, created = Asset.objects.get_or_create(
        cloud_id=cloud_id,
        defaults={
            'environment':    environment,
            'name':           asset_name,
            'provider':       provider,
            'asset_type':     asset_type,
            'asset_category': asset_category,
            'region':         region,
            'raw_data':       attributes,
            'memo':           f"Imported from tfstate: {environment.tfstate_filename}",
        }
    )
    if not created:
        # 上書き前に前回の raw_data を保存 → drift 比較に使う
        asset.raw_data_prev  = asset.raw_data
        asset.environment    = environment
        asset.name           = asset_name
        asset.provider       = provider
        asset.asset_type     = asset_type
        asset.asset_category = asset_category
        asset.region         = region
        asset.raw_data       = attributes
        asset.last_imported_at = timezone.now()
        asset.memo           = f"Updated from tfstate: {environment.tfstate_filename}"
        asset.save()
    else:
        # 新規追加は raw_data_prev を空のまま（ADDED として扱う）
        asset.last_imported_at = timezone.now()
        asset.save(update_fields=['last_imported_at'])

    return asset


def _process_tfstate_data(tfstate_data, environment):
    """1パス処理（Detail FK 依存なし）— 全プロバイダー対応。"""
    processed_count = 0
    for resource_data in tfstate_data.get('resources', []):
        if resource_data.get('mode') == 'managed' and resource_data.get('instances'):
            try:
                _create_or_update_asset(resource_data, environment)
                processed_count += 1
            except Exception as e:
                print(f"Error processing {resource_data.get('type')}/{resource_data.get('name')}: {e}")
    return processed_count


def _render_upload_form_error(request, message):
    values = {
        'system_name': request.POST.get('system_name', ''),
        'env_name':    request.POST.get('env_name', ''),
        'env_type':    request.POST.get('env_type', ''),
    }
    return render(request, '_upload_tfstate_form.html', {'error': message, 'values': values})


@require_POST
@htmx_login_required
def upload_tfstate_view(request):
    if 'tfstate_file' not in request.FILES:
        return _render_upload_form_error(request, _("No file selected."))

    tfstate_file = request.FILES['tfstate_file']
    if not (tfstate_file.name.endswith('.tfstate') or tfstate_file.name.endswith('.json')):
        return _render_upload_form_error(request, _("Only .tfstate or .json files are accepted."))

    try:
        tfstate_content = tfstate_file.read().decode('utf-8')
        tfstate_data    = json.loads(tfstate_content)
    except json.JSONDecodeError:
        return _render_upload_form_error(request, _("Failed to parse JSON. Please verify the file is a valid .tfstate."))
    except Exception as e:
        return _render_upload_form_error(request, _("File read error: %(err)s") % {'err': e})

    filename       = tfstate_file.name
    tfstate_config = _extract_tfstate_config(tfstate_data, filename) or {}

    override_system = request.POST.get('system_name', '').strip()
    override_env    = request.POST.get('env_name', '').strip()
    override_type   = request.POST.get('env_type', '').strip()
    if override_system:
        tfstate_config['system'] = override_system
        tfstate_config['code']   = slugify(override_system)[:50]
    if override_env:
        tfstate_config['env'] = override_env
    if override_type and override_type in dict(Environment.EnvType.choices):
        tfstate_config['env_type'] = override_type
    tfstate_config.setdefault('file', filename)

    if not tfstate_config.get('system') or not tfstate_config.get('env'):
        return _render_upload_form_error(
            request,
            _("Could not auto-detect system or environment name. Please fill in System Name and Environment Name manually.")
        )

    # 機密情報チェック
    secrets_found = _detect_secrets(tfstate_data)
    if secrets_found:
        # セッションに一時保存して警告を表示（DB には書かない）
        request.session['pending_tfstate'] = {
            'data':   tfstate_data,
            'config': tfstate_config,
        }
        return render(request, '_upload_secret_warning.html', {
            'secrets': secrets_found,
            'filename': filename,
        })

    return _do_import_tfstate(request, tfstate_data, tfstate_config, filename)


@require_POST
@htmx_login_required
@transaction.atomic
def confirm_upload_tfstate_view(request):
    """機密情報警告を確認後、スクラブしてインポートする。"""
    pending = request.session.pop('pending_tfstate', None)
    if not pending:
        return _render_upload_form_error(request, _("Session expired. Please upload the file again."))
    return _do_import_tfstate(
        request,
        pending['data'],
        pending['config'],
        pending['config'].get('file', 'unknown.tfstate'),
    )


@transaction.atomic
def _do_import_tfstate(request, tfstate_data, tfstate_config, filename):
    """スクラブ済みデータをDBに書き込む共通処理。"""
    try:
        org = _get_user_org(request)
        system, environment = _get_or_create_system_and_environment(tfstate_config, org=org)
        if tfstate_config.get('env_type') in dict(Environment.EnvType.choices):
            environment.env_type = tfstate_config['env_type']
            environment.save(update_fields=['env_type'])
        processed_count = _process_tfstate_data(tfstate_data, environment)
        from .models import DriftSnapshot
        _record_drift_snapshot(environment, DriftSnapshot.Source.TFSTATE)
    except Exception as e:
        return _render_upload_form_error(request, _("An error occurred during processing: %(err)s") % {'err': e})

    assets = Asset.objects.select_related('environment', 'environment__system').filter(
        environment_id=environment.id
    ).order_by('-updated_at')
    context = {
        'assets':               assets,
        'selected_system':      system,
        'selected_environment': environment,
        'provider_choices':     get_provider_choices(),
        'message': _('%(count)d asset(s) registered/updated from %(filename)s.') % {
            'filename': filename, 'count': processed_count,
        },
    }
    response = render(request, '_asset_list.html', context)
    response['HX-Retarget'] = '#main-content'
    response['HX-Reswap']   = 'innerHTML'
    response['HX-Trigger']  = 'closeUploadModal'
    return response


@require_GET
@htmx_login_required
def upload_form_view(request):
    return render(request, '_upload_tfstate_form.html')


# ---------------------------------------------------------------------------
# System CRUD
# ---------------------------------------------------------------------------

@require_GET
@htmx_login_required
def create_system_form_view(request):
    return render(request, '_system_create_form.html')


@require_POST
@htmx_login_required
def create_system_view(request):
    name             = request.POST.get('name', '').strip()
    code             = request.POST.get('code', '').strip() or slugify(name)[:50] or 'system'
    aws_role_arn     = request.POST.get('aws_role_arn', '').strip() or None
    regions_raw      = request.POST.get('aws_scan_regions', '').strip()
    aws_scan_regions = [r.strip() for r in regions_raw.split(',') if r.strip()]
    org = _get_user_org(request)

    errors = {}
    if not name:
        errors['name'] = _('System name is required.')
    if System.objects.filter(name=name).exists():
        errors['name'] = _('This system name is already in use.')
    if System.objects.filter(code=code).exists():
        errors['code'] = _('This code is already in use.')

    if errors:
        return render(request, '_system_create_form.html', {'errors': errors, 'values': request.POST})

    System.objects.create(
        name=name, code=code,
        aws_role_arn=aws_role_arn,
        aws_scan_regions=aws_scan_regions,
        organization=org,
    )
    return render(request, '_system_list.html', _system_list_context(org))


_SCAN_INTERVAL_CHOICES = [
    (15,   gettext_lazy('15 min')),
    (30,   gettext_lazy('30 min')),
    (60,   gettext_lazy('1 hour')),
    (360,  gettext_lazy('6 hours')),
    (1440, gettext_lazy('24 hours')),
]


@require_GET
@htmx_login_required
def edit_system_form_view(request, system_id):
    system = _user_system_or_404(request, system_id)
    return render(request, '_system_edit_form.html', {
        'system': system,
        'scan_interval_choices': _SCAN_INTERVAL_CHOICES,
    })


@require_POST
@htmx_login_required
def update_system_view(request, system_id):
    system           = _user_system_or_404(request, system_id)
    name             = request.POST.get('name', '').strip()
    code             = request.POST.get('code', '').strip()
    aws_role_arn     = request.POST.get('aws_role_arn', '').strip() or None
    regions_raw      = request.POST.get('aws_scan_regions', '').strip()
    aws_scan_regions = [r.strip() for r in regions_raw.split(',') if r.strip()]
    org = _get_user_org(request)

    errors = {}
    if not name:
        errors['name'] = _('System name is required.')
    if not code:
        errors['code'] = _('Code is required.')
    if System.objects.filter(name=name).exclude(pk=system_id).exists():
        errors['name'] = _('This system name is already in use.')
    if System.objects.filter(code=code).exclude(pk=system_id).exists():
        errors['code'] = _('This code is already in use.')

    from asset_manager.notifications import _is_allowed_webhook
    slack_webhook_url = request.POST.get('slack_webhook_url', '').strip() or None
    if slack_webhook_url and not _is_allowed_webhook(slack_webhook_url):
        errors['slack_webhook_url'] = _('Webhook URL must be an https://hooks.slack.com/… address.')

    if errors:
        return render(request, '_system_edit_form.html', {'system': system, 'errors': errors})

    valid_intervals = {c[0] for c in _SCAN_INTERVAL_CHOICES}
    try:
        interval = int(request.POST.get('scan_interval_minutes', 60))
        if interval not in valid_intervals:
            interval = 60
    except ValueError:
        interval = 60

    slack_language = request.POST.get('slack_language', '')
    if slack_language not in System.SlackLang.values:
        slack_language = System.SlackLang.EN

    system.name                  = name
    system.code                  = code
    system.aws_role_arn          = aws_role_arn
    system.aws_scan_regions      = aws_scan_regions
    system.scan_enabled          = 'scan_enabled' in request.POST
    system.scan_interval_minutes = interval
    system.slack_webhook_url     = slack_webhook_url
    system.slack_language        = slack_language
    system.save()
    return render(request, '_system_list.html', _system_list_context(org))


@require_POST
@htmx_login_required
def delete_system_view(request, system_id):
    system = _user_system_or_404(request, system_id)
    org    = _get_user_org(request)
    system.delete()
    return render(request, '_system_list.html', _system_list_context(org))


# ---------------------------------------------------------------------------
# Environment CRUD
# ---------------------------------------------------------------------------

@require_GET
@htmx_login_required
def create_environment_form_view(request, system_id):
    system = _user_system_or_404(request, system_id)
    return render(request, '_environment_create_form.html', {
        'system': system,
        'env_types': Environment.EnvType.choices,
    })


@require_POST
@htmx_login_required
def create_environment_view(request, system_id):
    system   = _user_system_or_404(request, system_id)
    name     = request.POST.get('name', '').strip()
    env_type = request.POST.get('env_type', Environment.EnvType.DEV)

    backend_type = request.POST.get('backend_type', Environment.BackendType.MANUAL)
    s3_bucket    = request.POST.get('s3_bucket', '').strip() or None
    s3_key       = request.POST.get('s3_key', '').strip() or None
    s3_region    = request.POST.get('s3_region', '').strip() or None
    s3_auto_sync = request.POST.get('s3_auto_sync') == '1'

    errors = {}
    if not name:
        errors['name'] = _('Environment name is required.')
    if Environment.objects.filter(system=system, name=name).exists():
        errors['name'] = _('This environment name already exists.')

    if errors:
        return render(request, '_environment_create_form.html', {
            'system': system, 'env_types': Environment.EnvType.choices,
            'errors': errors, 'values': request.POST,
        })

    Environment.objects.create(
        system=system, name=name, env_type=env_type,
        backend_type=backend_type, s3_bucket=s3_bucket, s3_key=s3_key, s3_region=s3_region,
        s3_auto_sync=s3_auto_sync,
    )
    environments = Environment.objects.filter(system=system).order_by('env_type', 'name')
    return render(request, '_environment_list.html', {'system': system, 'environments': environments})


@require_GET
@htmx_login_required
def edit_environment_form_view(request, environment_id):
    env = _user_environment_or_404(request, environment_id)
    return render(request, '_environment_edit_form.html', {
        'env': env,
        'env_types': Environment.EnvType.choices,
    })


@require_POST
@htmx_login_required
def update_environment_view(request, environment_id):
    env          = _user_environment_or_404(request, environment_id)
    name         = request.POST.get('name', '').strip()
    env_type     = request.POST.get('env_type', env.env_type)
    backend_type = request.POST.get('backend_type', env.backend_type)
    s3_bucket    = request.POST.get('s3_bucket', '').strip() or None
    s3_key       = request.POST.get('s3_key', '').strip() or None
    s3_region    = request.POST.get('s3_region', '').strip() or None
    s3_auto_sync = request.POST.get('s3_auto_sync') == '1'

    errors = {}
    if not name:
        errors['name'] = _('Environment name is required.')
    if Environment.objects.filter(system=env.system, name=name).exclude(pk=environment_id).exists():
        errors['name'] = _('This environment name already exists.')

    if errors:
        return render(request, '_environment_edit_form.html', {
            'env': env, 'env_types': Environment.EnvType.choices, 'errors': errors,
        })

    env.name         = name
    env.env_type     = env_type
    env.backend_type = backend_type
    env.s3_bucket    = s3_bucket
    env.s3_key       = s3_key
    env.s3_region    = s3_region
    env.s3_auto_sync = s3_auto_sync
    env.save()
    environments = Environment.objects.filter(system=env.system).order_by('env_type', 'name')
    return render(request, '_environment_list.html', {'system': env.system, 'environments': environments})


@require_POST
@htmx_login_required
def delete_environment_view(request, environment_id):
    env    = _user_environment_or_404(request, environment_id)
    system = env.system
    env.delete()
    environments = Environment.objects.filter(system=system).order_by('env_type', 'name')
    return render(request, '_environment_list.html', {'system': system, 'environments': environments})


# ---------------------------------------------------------------------------
# Asset 詳細・作成・削除（raw_data のみ・Detail テーブル廃止済み）
# ---------------------------------------------------------------------------

@require_GET
@htmx_login_required
def asset_detail_view(request, asset_id):
    asset = _user_asset_or_404(request, asset_id)
    return render(request, '_asset_detail.html', {'asset': asset})


@require_GET
@htmx_login_required
def create_asset_form_view(request):
    environment_id = request.GET.get('environment_id')
    environment    = _user_environment_or_404(request, environment_id) if environment_id else None
    return render(request, '_asset_create_form.html', {
        'environment':  environment,
        'asset_types':  get_known_asset_types(),
        'providers':    get_provider_choices(),
        'environments': _user_environments(request).select_related('system').order_by('system__name', 'env_type'),
    })


@require_POST
@htmx_login_required
def create_asset_view(request):
    name           = request.POST.get('name', '').strip()
    asset_type     = request.POST.get('asset_type', 'OTHER')
    cloud_id       = request.POST.get('cloud_id', '').strip() or f"manual-{name}"
    region         = request.POST.get('region', '').strip() or None
    memo           = request.POST.get('memo', '').strip() or None
    environment_id = request.POST.get('environment_id') or None
    provider       = request.POST.get('provider', 'AWS')

    errors      = {}
    environment = None

    if not name:
        errors['name'] = _('Asset name is required.')

    if environment_id:
        try:
            environment = _user_environments(request).get(pk=environment_id)
        except Environment.DoesNotExist:
            errors['environment_id'] = _('The specified environment does not exist.')

    if errors:
        return render(request, '_asset_create_form.html', {
            'errors':       errors,
            'values':       request.POST,
            'asset_types':  get_known_asset_types(),
            'providers':    get_provider_choices(),
            'environments': _user_environments(request).select_related('system').order_by('system__name', 'env_type'),
            'environment':  environment,
        })

    asset = Asset.objects.create(
        name=name, asset_type=asset_type, cloud_id=cloud_id,
        region=region, memo=memo, environment=environment, provider=provider,
    )

    assets = (
        Asset.objects.select_related('environment', 'environment__system')
        .filter(environment=environment).order_by('-updated_at')
        if environment else
        Asset.objects.select_related('environment', 'environment__system').order_by('-updated_at')
    )
    selected_system = environment.system if environment else None
    return render(request, '_asset_list.html', {
        'assets':               assets,
        'selected_system':      selected_system,
        'selected_environment': environment,
        'provider_choices':     get_provider_choices(),
        'message': _('Asset "%(name)s" has been registered.') % {'name': asset.name},
    })


@require_POST
@htmx_login_required
def delete_asset_view(request, asset_id):
    asset       = _user_asset_or_404(request, asset_id)
    environment = asset.environment
    selected_system = environment.system if environment else None
    asset.delete()

    assets = (
        Asset.objects.select_related('environment', 'environment__system')
        .filter(environment=environment).order_by('-updated_at')
        if environment else
        Asset.objects.select_related('environment', 'environment__system').order_by('-updated_at')
    )
    return render(request, '_asset_list.html', {
        'assets':               assets,
        'selected_system':      selected_system,
        'selected_environment': environment,
        'provider_choices':     get_provider_choices(),
    })


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

@require_GET
@htmx_login_required
def application_list_view(request, system_id):
    system = _user_system_or_404(request, system_id)
    applications = Application.objects.filter(system=system).prefetch_related(
        'env_configs__environment',
        'env_configs__dependencies',
    ).order_by('name')
    return render(request, '_application_list.html', {
        'system': system,
        'applications': applications,
    })


@require_GET
@htmx_login_required
def all_applications_view(request):
    org = _get_user_org(request)
    base = Application.objects.filter(system__organization=org) if org else Application.objects.none()
    applications = base.select_related('system').prefetch_related(
        'env_configs__environment',
        'env_configs__dependencies',
    ).order_by('system__name', 'name')
    return render(request, '_all_applications.html', {'applications': applications})


# ---------------------------------------------------------------------------
# Architecture Diagram（raw_data から VPC/サブネット情報を取得）
# ---------------------------------------------------------------------------

def _raw(asset):
    return asset.raw_data or {}


# プロバイダー横断トポロジー定義
_VPC_TYPES     = frozenset({'VPC'})
_LB_TYPES      = frozenset({'ALB'})
_COMPUTE_TYPES = frozenset({'EC2', 'ECS', 'FARGATE', 'LAMBDA', 'EKS'})
_DB_TYPES      = frozenset({'RDS', 'AURORA', 'DYNAMODB', 'ELASTICACHE', 'REDSHIFT'})
_MANAGED_TYPES = frozenset({'S3'})                   # VPC外のマネージドサービス
_SKIP_TYPES    = frozenset({'SUBNET', 'TG', 'LISTENER',
                            'NAT_GW', 'VPC_EP'})     # 図に出さない


def _vpc_id_of(asset):
    r = _raw(asset)
    t = asset.asset_type
    # AWS
    if t in ('EC2', 'RDS', 'ALB', 'VPC'):
        return r.get('vpc_id')
    if t in ('ECS', 'FARGATE'):
        nc = r.get('network_configuration') or {}
        if isinstance(nc, list):
            nc = nc[0] if nc else {}
        return nc.get('vpc_id')
    return None


def _subnet_id_of(asset):
    r = _raw(asset)
    t = asset.asset_type
    if t == 'EC2':
        return r.get('subnet_id')
    if t in ('ALB', 'ECS', 'FARGATE'):
        subs = r.get('subnets') or []
        return subs[0] if subs else None
    return None


def _asset_node_label(asset):
    r = _raw(asset)
    t = asset.asset_type
    name = asset.name or t
    # AWS
    if t == 'EC2':
        it = r.get('instance_type', '')
        return f'{name}\n{it}' if it else name
    if t in ('RDS', 'AURORA'):
        eng = r.get('engine', '')
        return f'{name}\n{eng}'.strip()
    if t == 'EBS':
        vt = r.get('type', '')
        sz = r.get('size', '')
        return f'{name}\n{vt} {sz}GB'.strip() if vt else name
    if t == 'ALB':
        return f'{name}\n{r.get("scheme", "")}'
    return name


def _create_diagram_nodes(asset):
    if asset.asset_type in _SKIP_TYPES:
        return []
    try:
        if asset.asset_type in ('ECS', 'FARGATE'):
            from diagrams.aws.compute import ECS
            r     = _raw(asset)
            count = max(1, int(r.get('desired_count') or 1))
            label = f'{asset.name}\n{r.get("launch_type", "FARGATE")}'
            return [ECS(label) for _ in range(count)]
        node = _create_aws_node(asset.asset_type, _asset_node_label(asset))
        return [node] if node else []
    except Exception as e:
        logger.warning("Failed to build diagram node for asset %s: %s", getattr(asset, 'id', '?'), e)
    return []


def _create_aws_node(asset_type, label):
    from diagrams.aws.compute import EC2, ECS, Lambda
    from diagrams.aws.database import RDS, Dynamodb, ElastiCache
    from diagrams.aws.network import ELB, CloudFront, APIGateway
    from diagrams.aws.storage import S3, EBS
    mapping = {
        'EC2':        EC2,
        'ECS':        ECS,
        'FARGATE':    ECS,
        'LAMBDA':     Lambda,
        'RDS':        RDS,
        'AURORA':     RDS,
        'DYNAMODB':   Dynamodb,
        'ELASTICACHE':ElastiCache,
        'ALB':        ELB,
        'CLOUDFRONT': CloudFront,
        'API_GW':     APIGateway,
        'S3':         S3,
        'EBS':        EBS,
    }
    cls = mapping.get(asset_type)
    return cls(label) if cls else None


def _generate_diagram_svg(env, assets):
    import tempfile, base64
    from diagrams import Diagram, Cluster

    # スキップ対象・描画対象だけに絞る
    drawable = [a for a in assets if a.asset_type not in _SKIP_TYPES]
    if not drawable:
        return None

    by_type  = {}
    for a in drawable:
        by_type.setdefault(a.asset_type, []).append(a)

    # プロバイダー横断トポロジー
    vpcs    = [a for a in drawable if a.asset_type in _VPC_TYPES]
    lbs     = [a for a in drawable if a.asset_type in _LB_TYPES]
    compute = [a for a in drawable if a.asset_type in _COMPUTE_TYPES]
    dbs     = [a for a in drawable if a.asset_type in _DB_TYPES]
    managed = [a for a in drawable if a.asset_type in _MANAGED_TYPES]
    ebss    = by_type.get('EBS', [])
    ec2s    = by_type.get('EC2', [])
    others  = [a for a in drawable
               if a.asset_type not in _VPC_TYPES | _LB_TYPES | _COMPUTE_TYPES
               | _DB_TYPES | _MANAGED_TYPES | {'EBS'}]

    vpc_members: dict[int, list] = {}
    in_vpc: set[int] = set()
    if vpcs:
        if len(vpcs) == 1:
            # VPC/VNET が1つならスキップ以外の全アセットを収容
            vpc_members[vpcs[0].id] = [a for a in drawable if a.asset_type not in _VPC_TYPES]
            in_vpc = {a.id for a in vpc_members[vpcs[0].id]}
        else:
            for vpc in vpcs:
                members = [a for a in drawable
                           if a.asset_type not in _VPC_TYPES | _MANAGED_TYPES
                           and _vpc_id_of(a) == vpc.cloud_id]
                vpc_members[vpc.id] = members
                in_vpc.update(a.id for a in members)

    not_in_vpc = [a for a in drawable if a.id not in in_vpc and a.asset_type not in _VPC_TYPES]
    standalone_managed  = [a for a in not_in_vpc if a.asset_type in _MANAGED_TYPES]
    standalone_others   = [a for a in not_in_vpc if a.asset_type not in _MANAGED_TYPES]

    inet_lbs = [lb for lb in lbs if _raw(lb).get('scheme') == 'internet-facing']

    def _render_list(asset_list, nodes):
        for asset in asset_list:
            ns = _create_diagram_nodes(asset)
            if ns:
                nodes[asset.id] = ns

    def _group_by_subnet(members):
        groups, no_sub = {}, []
        for a in members:
            sid = _subnet_id_of(a)
            if sid:
                groups.setdefault(sid, []).append(a)
            else:
                no_sub.append(a)
        return groups, no_sub

    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, 'diagram')
        try:
            # インターネットゲートウェイ（inet facing LB がある場合）
            inet_node_cls = None
            if inet_lbs:
                from diagrams.aws.network import InternetGateway
                inet_node_cls = InternetGateway

            with Diagram(env.name, show=False, filename=out, outformat='png', direction='LR',
                         graph_attr={'pad': '0.5', 'splines': 'ortho', 'nodesep': '0.60',
                                     'ranksep': '0.75', 'bgcolor': 'transparent',
                                     'fontname': 'Sans-Serif', 'fontsize': '12'}):
                nodes: dict[int, list] = {}
                inet = inet_node_cls("Internet") if inet_lbs and inet_node_cls else None

                # VPC クラスター
                for vpc in vpcs:
                    members = vpc_members.get(vpc.id, [])
                    subnet_groups, no_sub = _group_by_subnet(members)
                    with Cluster(vpc.name):
                        for sid, sub_assets in subnet_groups.items():
                            with Cluster(f'Subnet {sid[:16]}'):
                                _render_list(sub_assets, nodes)
                        _render_list(no_sub, nodes)

                # VPC外のマネージドサービス
                if standalone_managed:
                    with Cluster('Managed Services'):
                        _render_list(standalone_managed, nodes)

                # その他スタンドアロン
                _render_list(standalone_others, nodes)

                # 接続: Internet → LB
                if inet:
                    for lb in inet_lbs:
                        if lb.id in nodes:
                            inet >> nodes[lb.id][0]

                # 接続: LB → Compute
                for lb in lbs:
                    for c in compute:
                        if lb.id in nodes and c.id in nodes:
                            c_nodes = nodes[c.id]
                            nodes[lb.id][0] >> (c_nodes if len(c_nodes) > 1 else c_nodes[0])

                # 接続: Compute → DB
                for c in compute:
                    for db in dbs:
                        if c.id in nodes and db.id in nodes:
                            for cn in nodes[c.id]:
                                cn >> nodes[db.id][0]

                # 接続: EC2 → EBS（アタッチメント情報）
                for ebs in ebss:
                    attached = _raw(ebs).get('attachments', [{}])
                    inst_id  = (attached[0] if attached else {}).get('instance_id')
                    if inst_id and ebs.id in nodes:
                        for ec2 in ec2s:
                            if ec2.cloud_id == inst_id and ec2.id in nodes:
                                nodes[ec2.id][0] >> nodes[ebs.id][0]
                                break

                # 接続: Compute → Managed（S3/Blob等）
                for c in compute:
                    for m in managed:
                        if c.id in nodes and m.id in nodes:
                            for cn in nodes[c.id]:
                                cn >> nodes[m.id][0]

        except Exception:
            return None

        png_path = out + '.png'
        if not os.path.exists(png_path):
            return None
        with open(png_path, 'rb') as f:
            return base64.b64encode(f.read()).decode('utf-8')


@require_GET
@htmx_login_required
def diagram_view(request, environment_id):
    env     = _user_environment_or_404(request, environment_id)
    assets  = list(Asset.objects.filter(environment=env))
    png_b64 = _generate_diagram_svg(env, assets) if assets else None
    return render(request, '_diagram.html', {'env': env, 'png_b64': png_b64})


# ---------------------------------------------------------------------------
# Drift Report
# ---------------------------------------------------------------------------

@require_GET
@htmx_login_required
def drift_report_view(request, environment_id):
    """
    環境単位の Drift レポート。
    - CHANGED : raw_data_prev と raw_data が異なる
    - ADDED   : raw_data_prev が空（= 前回インポート時に存在しなかった）
    - REMOVED : AWS 側から消えた（missing_since が立っている）
    - UNCHANGED: 変化なし
    """
    from .autoscaling import is_autoscaling_churn, autoscaling_group_of

    env    = _user_environment_or_404(request, environment_id)
    assets = env.assets.order_by('asset_type', 'name')

    added       = []
    changed     = []
    removed     = []
    autoscaling = []
    unchanged   = []

    for asset in assets:
        if asset.missing_since:
            # 消滅も「存在」次元なので、ASG 由来なら churn 側へ。
            if is_autoscaling_churn(asset.raw_data):
                autoscaling.append({'asset': asset,
                                    'group': autoscaling_group_of(asset.raw_data),
                                    'gone': True})
            else:
                removed.append({'asset': asset})
        elif not asset.raw_data_prev:
            # ASG-owned first-sighting is churn, not drift — shown in its own
            # section so it's transparent, not silently hidden.
            if is_autoscaling_churn(asset.raw_data):
                autoscaling.append({'asset': asset, 'group': autoscaling_group_of(asset.raw_data)})
            else:
                added.append({'asset': asset})
        else:
            diff = _compute_raw_diff(asset.raw_data_prev, asset.raw_data)
            if diff:
                changed.append({'asset': asset, 'changes': diff})
            else:
                unchanged.append({'asset': asset})

    return render(request, '_drift_report.html', {
        'environment': env,
        'added':       added,
        'changed':     changed,
        'removed':     removed,
        'autoscaling': autoscaling,
        'unchanged':   unchanged,
    })


def drift_history_view(request, environment_id):
    """環境のドリフト履歴（時系列の推移）。"""
    from .models import DriftSnapshot
    from .plugins import feature_enabled

    if not feature_enabled('drift_history'):
        raise Http404
    env       = _user_environment_or_404(request, environment_id)
    snapshots = list(DriftSnapshot.objects.filter(environment=env)[:100])

    # 推移バーの高さ正規化用に最大値を出す
    peak = max((s.total_count for s in snapshots), default=0)

    return render(request, '_drift_history.html', {
        'environment': env,
        'snapshots':   snapshots,
        'peak':        peak,
    })


def drift_snapshot_detail_view(request, environment_id, snapshot_id):
    """履歴上の1スナップショットの差分詳細（保存済み detail を描画）。"""
    from .models import DriftSnapshot
    from .plugins import feature_enabled

    if not feature_enabled('drift_history'):
        raise Http404
    env      = _user_environment_or_404(request, environment_id)
    snapshot = get_object_or_404(DriftSnapshot, pk=snapshot_id, environment=env)
    detail   = snapshot.detail or {}

    return render(request, '_drift_snapshot.html', {
        'environment': env,
        'snapshot':    snapshot,
        'changed':     detail.get('changed', []),
        'added':       detail.get('added', []),
        'removed':     detail.get('removed', []),
    })


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def login_view(request):
    if request.user.is_authenticated:
        return redirect('/')
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        user     = authenticate(request, username=username, password=password)
        if user is not None:
            try:
                if user.profile.two_factor_enabled and user.profile.totp_secret:
                    request.session['totp_pending_user_id'] = user.pk
                    next_url = request.GET.get('next', '/')
                    return redirect(f'/totp-verify/?next={next_url}')
            except UserProfile.DoesNotExist:
                pass
            auth_login(request, user)
            return redirect(request.GET.get('next') or '/')
        return render(request, 'login.html', {
            'error': _('Incorrect username or password.'),
            'username': username,
        })
    return render(request, 'login.html')


def totp_verify_view(request):
    pending_id = request.session.get('totp_pending_user_id')
    if not pending_id:
        return redirect('/login/')

    if request.method == 'POST':
        import pyotp
        code = request.POST.get('code', '').replace(' ', '')
        try:
            user    = _User.objects.get(pk=pending_id)
            profile = UserProfile.objects.get(user=user)
            if pyotp.TOTP(profile.totp_secret).verify(code):
                del request.session['totp_pending_user_id']
                auth_login(request, user)
                return redirect(request.GET.get('next') or '/')
        except (_User.DoesNotExist, UserProfile.DoesNotExist):
            return redirect('/login/')
        return render(request, 'totp_verify.html', {'error': _('認証コードが正しくありません')})

    return render(request, 'totp_verify.html')


@require_POST
def logout_view(request):
    auth_logout(request)
    return redirect('/login/')


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

def _get_or_create_profile(user):
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


@require_GET
@htmx_login_required
def profile_view(request):
    user       = request.user
    profile    = _get_or_create_profile(user)
    memberships = Membership.objects.filter(user=user).select_related('organization')
    is_owner   = memberships.filter(role=Membership.Role.OWNER).exists()
    return render(request, '_profile.html', {
        'profile':     profile,
        'memberships': memberships,
        'is_owner':    is_owner,
        'role_choices': Membership.Role.choices,
    })


@require_POST
@htmx_login_required
def profile_update_view(request):
    user = request.user
    user.first_name = request.POST.get('first_name', '').strip()
    user.last_name  = request.POST.get('last_name',  '').strip()
    new_email = request.POST.get('email', '').strip()
    if new_email:
        user.email = new_email
    user.save()

    return HttpResponse(
        '<p class="text-sm text-green-600 flex items-center gap-1">'
        '<svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">'
        '<path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/></svg>'
        f'{_("Saved")}</p>'
    )


@require_POST
@htmx_login_required
def password_change_view(request):
    user    = request.user
    current = request.POST.get('current_password', '')
    new_pw  = request.POST.get('new_password', '')
    confirm = request.POST.get('confirm_password', '')

    def _err(msg):
        return HttpResponse(f'<p class="text-sm text-red-600">{msg}</p>', status=200)

    if not user.check_password(current):
        return _err(_('Current password is incorrect'))
    if len(new_pw) < 8:
        return _err(_('New password must be at least 8 characters'))
    if new_pw != confirm:
        return _err(_('New passwords do not match'))

    user.set_password(new_pw)
    user.save()
    update_session_auth_hash(request, user)

    return HttpResponse(
        '<p class="text-sm text-green-600 flex items-center gap-1">'
        '<svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">'
        '<path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/></svg>'
        f'{_("Password changed")}</p>'
    )


@require_GET
@htmx_login_required
def totp_setup_view(request):
    import pyotp, qrcode, io, base64
    secret = pyotp.random_base32()
    request.session['totp_pending_secret'] = secret

    name   = request.user.email or request.user.username
    uri    = pyotp.TOTP(secret).provisioning_uri(name=name, issuer_name='Cloud Asset Manager')
    buf    = io.BytesIO()
    qrcode.make(uri).save(buf, format='PNG')
    qr_b64 = base64.b64encode(buf.getvalue()).decode()

    return render(request, '_profile_totp_setup.html', {'secret': secret, 'qr_b64': qr_b64})


@require_POST
@htmx_login_required
def totp_confirm_view(request):
    import pyotp
    secret = request.session.get('totp_pending_secret')
    code   = request.POST.get('code', '').replace(' ', '')

    if not secret:
        return HttpResponse(f'<p class="text-sm text-red-600">{_("Your session has expired. Please try again.")}</p>')

    if not pyotp.TOTP(secret).verify(code):
        return render(request, '_profile_totp_setup.html', {
            'secret': secret,
            'qr_b64': request.POST.get('qr_b64', ''),
            'error':  _('The verification code is incorrect'),
        })

    profile = _get_or_create_profile(request.user)
    profile.totp_secret        = secret
    profile.two_factor_enabled = True
    profile.save()
    request.session.pop('totp_pending_secret', None)

    return HttpResponse(
        '<div class="flex items-center gap-3 p-3 bg-green-50 rounded-xl border border-green-200">'
        f'<span class="text-green-700 text-sm font-medium">✓ {_("Two-factor authentication enabled")}</span>'
        '</div>'
        '<script>document.getElementById("profile-2fa-section").dispatchEvent(new CustomEvent("2fa-enabled"))</script>'
    )


@require_POST
@htmx_login_required
def totp_disable_view(request):
    password = request.POST.get('password', '')
    if not request.user.check_password(password):
        return HttpResponse(f'<p class="text-sm text-red-600 mt-2">{_("Password is incorrect")}</p>')

    profile = _get_or_create_profile(request.user)
    profile.two_factor_enabled = False
    profile.totp_secret        = ''  # nosec B105 - 2FA無効化のため空にクリア（ハードコード秘密ではない）
    profile.save()

    return render(request, '_profile_2fa_status.html', {'profile': profile})


@require_POST
@htmx_login_required
def account_delete_view(request):
    user     = request.user
    password = request.POST.get('password', '')

    if not user.check_password(password):
        return HttpResponse(f'<p class="text-sm text-red-600 mt-2">{_("Password is incorrect")}</p>')

    if request.POST.get('delete_assets') == '1':
        owner_org_ids = Membership.objects.filter(
            user=user, role=Membership.Role.OWNER
        ).values_list('organization_id', flat=True)
        sys_ids = System.objects.filter(organization_id__in=owner_org_ids).values_list('id', flat=True)
        env_ids = Environment.objects.filter(system_id__in=sys_ids).values_list('id', flat=True)
        Asset.objects.filter(environment_id__in=env_ids).delete()

    auth_logout(request)
    user.delete()

    response = HttpResponse()
    response['HX-Redirect'] = '/login/'
    return response


@require_POST
@htmx_login_required
def membership_update_view(request, membership_id):
    target = get_object_or_404(Membership, pk=membership_id)

    try:
        requester = Membership.objects.get(user=request.user, organization=target.organization)
    except Membership.DoesNotExist:
        return HttpResponseForbidden()
    if requester.role != Membership.Role.OWNER:
        return HttpResponseForbidden()

    new_role = request.POST.get('role', '')
    if new_role not in dict(Membership.Role.choices):
        return HttpResponseBadRequest()

    if target.role == Membership.Role.OWNER and new_role != Membership.Role.OWNER:
        owner_count = Membership.objects.filter(
            organization=target.organization, role=Membership.Role.OWNER
        ).count()
        if owner_count <= 1:
            return HttpResponse(f'<p class="text-sm text-red-600">{_("Cannot change the role of the last owner")}</p>', status=400)

    target.role = new_role
    target.save()
    return HttpResponse(
        f'<span class="text-xs font-semibold px-2 py-0.5 rounded-full bg-indigo-100 text-indigo-700">'
        f'{target.get_role_display()}</span>'
        f'<span class="text-xs text-green-600 ml-2">{_("Updated")}</span>'
    )


# ---------------------------------------------------------------------------
# Audit Log
# ---------------------------------------------------------------------------

MODEL_LABELS = {
    'Asset':        'Asset',
    'System':       'System',
    'Environment':  'Environment',
    'Application':  'Application',
    'AppEnvConfig': 'App Config',
    'AppDependency':'Dependency',
}


@require_GET
@htmx_login_required
def audit_log_view(request):
    from django.core.paginator import Paginator
    org = _get_user_org(request)
    if org is None:
        qs = AuditLog.objects.none()
    else:
        # 自組織メンバーが行った変更のみ（AuditLog にテナント列が無いため user 経由で絞る）
        qs = _safe_query_or_empty(
            lambda: AuditLog.objects.select_related('user')
                                    .filter(user__memberships__organization=org)
                                    .distinct()
        )

    action_filter = request.GET.get('action', '')
    model_filter  = request.GET.get('model', '')
    user_filter   = request.GET.get('user', '')

    if action_filter:
        qs = qs.filter(action=action_filter)
    if model_filter:
        qs = qs.filter(model_name=model_filter)
    if user_filter:
        qs = qs.filter(user__username__icontains=user_filter)

    paginator = Paginator(qs, 50)
    page_obj  = paginator.get_page(request.GET.get('page', 1))

    return render(request, '_audit_log.html', {
        'page_obj':      page_obj,
        'action_filter': action_filter,
        'model_filter':  model_filter,
        'user_filter':   user_filter,
        'model_choices': list(MODEL_LABELS.keys()),
        'model_labels':  MODEL_LABELS,
    })


# ---------------------------------------------------------------------------
# Boto3 Scan trigger
# ---------------------------------------------------------------------------

@require_POST
@htmx_login_required
def trigger_scan_view(request, environment_id):
    from .scanner import run_scan
    from .models import ScanJob

    env    = _user_environment_or_404(request, environment_id)
    system = env.system

    if not system.aws_role_arn and not (
        __import__('os').getenv('AWS_ACCESS_KEY_ID') or
        __import__('os').getenv('AWS_PROFILE')
    ):
        return render(request, '_scan_result.html', {
            'result': {'errors': [_('AWS credentials are not configured. Enter a Role ARN in the System settings.')],
                       'created': 0, 'updated': 0, 'scanned': 0},
        })

    job = ScanJob.objects.create(
        system=system,
        status=ScanJob.Status.RUNNING,
        regions=system.aws_scan_regions or [],
        started_at=timezone.now(),
    )
    try:
        result = run_scan(system, env)
        job.status        = ScanJob.Status.DONE
        job.created_count = result['created']
        job.updated_count = result['updated']
        job.finished_at   = timezone.now()
        if result['errors']:
            job.error_message = '\n'.join(result['errors'])
        job.save()
        # Drift 履歴を記録
        from .models import DriftSnapshot
        _record_drift_snapshot(env, DriftSnapshot.Source.SCAN)
        # Drift 通知
        from .notifications import send_drift_notification
        send_drift_notification(system, env, result)
    except Exception as e:
        job.status        = ScanJob.Status.FAILED
        job.error_message = str(e)
        job.finished_at   = timezone.now()
        job.save()
        result = {'errors': [str(e)], 'created': 0, 'updated': 0, 'scanned': 0}

    return render(request, '_scan_result.html', {'result': result, 'job': job})


# ---------------------------------------------------------------------------
# S3 Remote State sync
# ---------------------------------------------------------------------------

def _fetch_tfstate_from_s3(env):
    """S3バックエンドから tfstate を取得して dict を返す。失敗時は例外を上げる。"""
    import boto3 as _boto3
    s3 = _boto3.client('s3', region_name=env.s3_region or 'us-east-1')
    obj = s3.get_object(Bucket=env.s3_bucket, Key=env.s3_key)
    return json.loads(obj['Body'].read())


def sync_s3_state_core(env):
    """S3からtfstateを取得してインポートする。HTTPリクエスト不要 — スケジューラーからも呼べる。
    Returns: {'errors': [...], 'created': int, 'updated': int, 'scanned': int}
    """
    if env.backend_type != Environment.BackendType.S3:
        return {'errors': [_('This environment is not configured with an S3 backend.')], 'created': 0, 'updated': 0, 'scanned': 0}
    if not env.s3_bucket or not env.s3_key:
        return {'errors': [_('Please set the S3 bucket and key.')], 'created': 0, 'updated': 0, 'scanned': 0}

    try:
        tfstate_data = _fetch_tfstate_from_s3(env)
    except Exception as e:
        return {'errors': [f'{_("Failed to fetch from S3")}: {e}'], 'created': 0, 'updated': 0, 'scanned': 0}

    secrets_found = _detect_secrets(tfstate_data)
    if secrets_found:
        return {'errors': [f'{_("tfstate contains sensitive information")}: {", ".join(secrets_found.keys())}'], 'created': 0, 'updated': 0, 'scanned': 0}

    try:
        count = _process_tfstate_data(tfstate_data, env)
        env.tfstate_filename = f's3://{env.s3_bucket}/{env.s3_key}'
        env.save(update_fields=['tfstate_filename'])
        from .models import DriftSnapshot
        _record_drift_snapshot(env, DriftSnapshot.Source.S3SYNC)
    except Exception as e:
        return {'errors': [f'{_("Import error")}: {e}'], 'created': 0, 'updated': 0, 'scanned': 0}

    return {'errors': [], 'created': count, 'updated': 0, 'scanned': count, 's3_sync': True}


@require_POST
@htmx_login_required
def sync_s3_state_view(request, environment_id):
    env = _user_environment_or_404(request, environment_id)
    result = sync_s3_state_core(env)
    return render(request, '_scan_result.html', {'result': result})


# ---------------------------------------------------------------------------
# Sample tfstate ライブラリ
# ---------------------------------------------------------------------------

from pathlib import Path
from django.conf import settings as _dj_settings
from django.http import FileResponse, Http404

_SAMPLES_DIR = Path(_dj_settings.BASE_DIR) / 'fixtures' / 'tfstates' / 'aws'

# ファイル名 → (ja表示名, en表示名, 環境タイプ)
# NOTE: サンプルは「seed に無いシステムを後から足す」用途。
# ECサイト は seed が看板デモとして作成するため、サンプルから除外する
# （同名で取り込むと既存システムに並行環境が増えて紛らわしいため）。
_SAMPLE_META = {
    'payment-prod.tfstate':          ('決済システム',      'Payment System',       'PROD'),
    'payment-stg.tfstate':           ('決済システム',      'Payment System',       'STG'),
    'mobile-api-prod.tfstate':       ('モバイルAPI',       'Mobile API',           'PROD'),
    'mobile-api-stg.tfstate':        ('モバイルAPI',       'Mobile API',           'STG'),
    'crm-prod.tfstate':              ('CRMシステム',       'CRM System',           'PROD'),
    'crm-stg.tfstate':               ('CRMシステム',       'CRM System',           'STG'),
    'hr-system-prod.tfstate':        ('人事システム',      'HR System',            'PROD'),
    'hr-system-stg.tfstate':         ('人事システム',      'HR System',            'STG'),
    'analytics-prod.tfstate':        ('データ分析基盤',    'Analytics Platform',   'PROD'),
    'data-pipeline-prod.tfstate':    ('データパイプライン','Data Pipeline',         'PROD'),
    'financial-prod.tfstate':        ('財務システム',      'Financial System',     'PROD'),
    'logistics-api-prod.tfstate':    ('物流API',           'Logistics API',        'PROD'),
    'notification-prod.tfstate':     ('通知サービス',      'Notification Service', 'PROD'),
    'notification-stg.tfstate':      ('通知サービス',      'Notification Service', 'STG'),
    'content-platform-prod.tfstate': ('コンテンツ配信',    'Content Platform',     'PROD'),
    'internal-portal-prod.tfstate':  ('社内ポータル',      'Internal Portal',      'PROD'),
}

# 環境タイプ → バッジ色
_ENV_COLORS = {
    'PROD': 'bg-green-100 text-green-700',
    'STG':  'bg-amber-100 text-amber-700',
    'DEV':  'bg-blue-100 text-blue-700',
}


def _sample_resource_summary(data):
    """tfstate dict → {asset_type: count} の要約を返す。"""
    counts = {}
    for r in data.get('resources', []):
        if r.get('mode') == 'managed' and r.get('instances'):
            asset_type, _ = resolve_resource_type(r['type'], r['instances'][0].get('attributes', {}))
            counts[asset_type] = counts.get(asset_type, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: -x[1]))


def _load_sample(filename):
    """ファイル名を検証してJSONを返す。存在しない / 不正なら None。"""
    if '/' in filename or '\\' in filename or not filename.endswith('.tfstate'):
        return None
    path = _SAMPLES_DIR / filename
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None


def _build_sample_list(lang):
    """言語コードに応じた表示名でサンプル一覧を構築して返す。"""
    is_ja = lang.startswith('ja')
    samples = []
    for filename, (ja, en, env_type) in _SAMPLE_META.items():
        data = _load_sample(filename)
        if data is None:
            continue
        summary = _sample_resource_summary(data)
        samples.append({
            'filename':  filename,
            'name':      ja if is_ja else en,
            'env_type':  env_type,
            'env_color': _ENV_COLORS.get(env_type, 'bg-slate-100 text-slate-600'),
            'summary':   summary,
            'total':     sum(summary.values()),
        })
    return samples


@require_GET
@htmx_login_required
def sample_list_view(request):
    lang    = request.LANGUAGE_CODE
    samples = _build_sample_list(lang)
    return render(request, '_sample_list.html', {'samples': samples})


@require_GET
@htmx_login_required
def sample_viewer_view(request, filename):
    data = _load_sample(filename)
    if data is None:
        raise Http404
    lang    = request.LANGUAGE_CODE
    is_ja   = lang.startswith('ja')
    meta    = _SAMPLE_META.get(filename, ('', '', ''))
    context = {
        'filename':     filename,
        'name':         meta[0] if is_ja else meta[1],
        'env_type':     meta[2],
        'env_color':    _ENV_COLORS.get(meta[2], ''),
        'json_content': json.dumps(data, indent=2, ensure_ascii=False),
        'summary':      _sample_resource_summary(data),
    }
    return render(request, '_sample_viewer.html', context)


@require_GET
@htmx_login_required
def sample_download_view(request, filename):
    path = _SAMPLES_DIR / filename
    if not path.exists() or '/' in filename or not filename.endswith('.tfstate'):
        raise Http404
    return FileResponse(
        open(path, 'rb'),
        as_attachment=True,
        filename=filename,
        content_type='application/json',
    )


@require_POST
@htmx_login_required
@transaction.atomic
def import_sample_view(request, filename):
    data = _load_sample(filename)
    if data is None:
        raise Http404

    lang    = request.LANGUAGE_CODE
    is_ja   = lang.startswith('ja')
    meta    = _SAMPLE_META.get(filename, ('Sample', 'Sample', 'PROD'))
    sys_name = meta[0] if is_ja else meta[1]
    env_type = meta[2]

    org = _get_user_org(request)
    tfstate_config = {
        'system':   sys_name,
        'code':     slugify(sys_name + '-sample')[:50],
        'env':      env_type,
        'env_type': env_type,
        'file':     filename,
    }
    system, environment = _get_or_create_system_and_environment(tfstate_config, org=org)
    processed = _process_tfstate_data(data, environment)

    return render(request, '_sample_import_result.html', {
        'name':      sys_name,
        'env_type':  env_type,
        'processed': processed,
        'system_id': system.id,
    })
