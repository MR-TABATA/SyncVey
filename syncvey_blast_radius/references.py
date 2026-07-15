"""
references.py
-------------
Step 1 of blast-radius: turn one asset's raw attributes into the set of *other*
cloud resources it points at (by ID or ARN).

Pure function over a single ``raw_data`` dict — no DB, no AWS, no graph — so it's
fast and exhaustively testable, like ``syncvey_drift_risk.rules``. Reverse-
indexing these IDs against sibling assets (edges) and BFS propagation from a
drifted node live in later steps.

Why value-scanning instead of the tfstate ``dependencies`` list: real tfstate
carries an explicit ``dependencies`` array per instance, but SyncVey's importer
keeps only ``attributes`` and drops ``dependencies`` (see
``views._import_or_update_asset``). We recover most of that graph straight from
the attribute values, which embed the referenced subnet / security-group / vpc
IDs and ARNs. If the importer is later taught to retain ``dependencies``, this
stays valid and complementary rather than redundant.
"""

import re

# Attribute keys that hold the resource's *own* identity, never a reference out.
SELF_KEYS = ('id', 'arn')

# An AWS ARN: arn:partition:service:region:account:resource…
_ARN_RE = re.compile(r'^arn:aws[a-z-]*:[^:]*:[^:]*:[^:]*:.+', re.IGNORECASE)

# Short-ID families for the resource types SyncVey tracks and links between.
# Extend this tuple to widen coverage; unknown IDs simply never match an asset
# in the reverse index and are dropped as dangling edges downstream.
_SHORT_ID_PREFIXES = (
    'vpc', 'subnet', 'sg', 'i', 'vol', 'igw', 'nat', 'eni', 'rtb', 'acl',
    'eipalloc', 'tgw', 'ami', 'snap', 'dopt', 'pcx', 'vpce', 'fs', 'fsvol',
)
_SHORT_ID_RE = re.compile(
    r'^(?:%s)-[0-9a-f]{6,}$' % '|'.join(_SHORT_ID_PREFIXES)
)


def looks_like_reference(value) -> bool:
    """True if a scalar value looks like a cloud resource ID or ARN."""
    if not isinstance(value, str):
        return False
    v = value.strip()
    return bool(_ARN_RE.match(v) or _SHORT_ID_RE.match(v))


def extract_reference_edges(raw_data) -> list:
    """
    ``[(field, referenced_id), ...]`` for every reference found in ``raw_data``,
    keeping which attribute each reference came from (used later to label edges).

    - Recurses into nested lists and dicts (e.g. ``network_interface`` blocks),
      but always labels the edge with the outermost attribute name.
    - Excludes the resource's own identity (its ``id`` / ``arn`` values) and
      meta keys (``_resource_type`` etc.).
    - Order-stable and de-duplicated on ``(field, id)``.
    """
    if not isinstance(raw_data, dict):
        return []

    self_ids = {
        raw_data.get(k) for k in SELF_KEYS if isinstance(raw_data.get(k), str)
    }

    edges = []
    seen = set()

    def visit(field, value):
        if isinstance(value, str):
            if looks_like_reference(value) and value not in self_ids:
                key = (field, value)
                if key not in seen:
                    seen.add(key)
                    edges.append(key)
        elif isinstance(value, list):
            for item in value:
                visit(field, item)
        elif isinstance(value, dict):
            for k, v in value.items():
                if isinstance(k, str) and k.startswith('_'):
                    continue
                visit(field, v)

    for k, v in raw_data.items():
        # Skip identity fields and scanner/importer meta keys (leading '_').
        if isinstance(k, str) and (k.startswith('_') or k in SELF_KEYS):
            continue
        visit(k, v)

    return edges


def extract_references(raw_data) -> set:
    """
    Set of referenced resource IDs/ARNs found anywhere in ``raw_data``,
    excluding the resource's own identity. Convenience wrapper over
    :func:`extract_reference_edges` when the source field doesn't matter.
    """
    return {ref for _field, ref in extract_reference_edges(raw_data)}
