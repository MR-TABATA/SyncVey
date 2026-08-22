"""
drift.py
--------
What counts as drift, decided in one place.

Two halves live here:

  * ``_compute_raw_diff`` — the *attribute* question: did the values change?
  * ``classify``          — the *existence* question (did it appear? disappear?)
    and, crucially, the order between the two.

The order is the part that kept getting rewritten. Four callers used to answer
it independently — the environment badge, the snapshot writer, the drift report
and the CLI — and every time the core learned a new category, some of those
copies kept the old answer. ``removed`` shipped in 0.1.0; two copies were
brought up to date in #24; the fourth was still calling a *deleted* resource an
*addition* when #31 caught it. The failure mode is always the same: a resource
that vanished has no ``raw_data_prev`` either, so judging on that field alone
makes a deletion look like a first sighting.

So callers no longer decide. They ask, and keep only their own output shape:
counts for the badge, JSON for the CLI, ORM rows for the report.
"""

from .autoscaling import is_autoscaling_churn

# 判定結果。呼び出し側はこの5つだけを知っていればよい。
CHANGED = 'changed'
ADDED = 'added'
REMOVED = 'removed'
AUTOSCALING = 'autoscaling'
UNCHANGED = 'unchanged'

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


def _compute_raw_diff(old: dict, new: dict) -> list:
    """
    old / new の raw_data を比較し、変更フィールドのリストを返す。
    [{'field': str, 'old': str, 'new': str}, ...]

    キーは「両方に存在するもの」の積集合(&)だけを比較する。
    tfstate インポートは全属性(50+キー)を保存する一方、ライブスキャン
    (scanner.py)は厳選した互換キー(~11)のみを出力するため、和集合(|)で
    比較すると scanner が出さない tfstate 固有キーが全て「削除」と誤検知
    される。実ドリフトは共通キー上で起きるので積集合で比較するのが正しい。
    新規/削除リソースの検出は classify() が存在次元として別途行う。
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


def classify(asset) -> tuple[str, list]:
    """
    1つの資産のドリフト区分を返す: ``(category, changes)``.

    ``changes`` は CHANGED のときだけ中身が入る（それ以外は空リスト）。

    存在を先に、属性を後に見る。この順序が本体で、逆にすると
    「AWS から消えた資産」が「初めて見た資産」と同じ枝に落ちる
    （どちらも raw_data_prev が空のため）。

    Auto Scaling が湧かせた／消したインスタンスは、出現も消滅も churn として
    AUTOSCALING に逃がす。抑制するのは存在次元だけで、生き続けている
    ASG インスタンスの属性変更は今も本物のドリフトとして CHANGED になる。
    """
    if asset.missing_since:
        # AWS から返って来なくなった。スケールインなら churn。
        return (AUTOSCALING if is_autoscaling_churn(asset.raw_data) else REMOVED), []
    if not asset.raw_data_prev:
        # 初回検出。スケールアウトなら churn。
        return (AUTOSCALING if is_autoscaling_churn(asset.raw_data) else ADDED), []
    changes = _compute_raw_diff(asset.raw_data_prev, asset.raw_data)
    return (CHANGED, changes) if changes else (UNCHANGED, [])
