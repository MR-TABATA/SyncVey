"""
graph.py
--------
Step 2 of blast-radius: turn a flat collection of assets into a directed
reference graph, by resolving each asset's outbound references (step 1,
:func:`references.extract_reference_edges`) against a reverse index of sibling
assets keyed by ``cloud_id`` within the same environment.

Pure over an iterable of asset-like records — anything exposing ``cloud_id``,
``environment_id`` and ``raw_data`` (real ``Asset`` rows or plain stubs) — so it
stays DB-free and exhaustively testable, like step 1 and ``syncvey_drift_risk``.

Why environment-scoped: ``Asset.cloud_id`` is globally unique in SyncVey, but a
real reference only ever points at a resource in the same account/environment
(and the importer sets ``cloud_id = attrs['id'] or attrs['arn']``, exactly the
shape step 1 extracts, so the reverse lookup lands). Scoping the index by
``environment_id`` keeps the graph honest and drops cross-environment
coincidences as dangling edges. BFS propagation from a drifted node (step 3)
walks this adjacency.
"""

from collections import namedtuple

from .references import extract_reference_edges

# A resolved directed edge: ``src`` references ``dst`` via attribute ``field``.
# Both endpoints are ``cloud_id`` strings held as assets in the same environment.
Edge = namedtuple('Edge', ('src', 'dst', 'field'))

# cloud_id values that are placeholders, never real reference targets.
_EMPTY_IDS = ('', '-', None)


def _env_key(asset):
    """Environment identity used to scope the reverse index (``None``-safe)."""
    return getattr(asset, 'environment_id', None)


def _cloud_id(asset):
    cid = getattr(asset, 'cloud_id', None)
    return cid if cid not in _EMPTY_IDS else None


def build_reverse_index(assets) -> dict:
    """
    ``{environment_id: {cloud_id: asset}}`` for resolving a reference to the
    sibling asset it points at.

    Placeholder ``cloud_id`` values (``''`` / ``'-'`` / ``None``) are skipped.
    For a repeated ``(environment_id, cloud_id)`` the first asset seen wins;
    ``cloud_id`` is unique in the DB, so this only guards against test stubs.
    """
    index = {}
    for asset in assets:
        cid = _cloud_id(asset)
        if cid is None:
            continue
        index.setdefault(_env_key(asset), {}).setdefault(cid, asset)
    return index


def build_edges(assets) -> list:
    """
    Resolve every asset's outbound references against sibling assets in the same
    environment → list of :class:`Edge`.

    - ``assets`` is walked twice (index, then edges), so pass a re-iterable
      collection (list / queryset), not a one-shot generator.
    - References that don't resolve to a sibling ``cloud_id`` — unknown ID
      families, resources SyncVey doesn't track, or cross-environment IDs — are
      dropped as dangling; the graph only holds edges between assets it has.
    - Self-edges are skipped (step 1 already drops an asset's own identity; this
      is belt-and-braces against a resource that lists its own id in a field).
    - De-duplicated on ``(src, dst, field)``; order-stable in asset iteration
      order, then reference order within each asset.
    """
    assets = list(assets)
    index = build_reverse_index(assets)

    edges = []
    seen = set()
    for asset in assets:
        src = _cloud_id(asset)
        if src is None:
            continue
        siblings = index.get(_env_key(asset), {})
        for field, ref in extract_reference_edges(getattr(asset, 'raw_data', None)):
            if ref == src or ref not in siblings:
                continue
            key = (src, ref, field)
            if key in seen:
                continue
            seen.add(key)
            edges.append(Edge(src, ref, field))
    return edges


def build_adjacency(assets, undirected: bool = True) -> dict:
    """
    Adjacency map ``{cloud_id: set(neighbour_cloud_id, ...)}`` built from
    :func:`build_edges`, ready for graph traversal in step 3.

    Blast radius flows both ways — an instance references its subnet, but a
    subnet's drift blasts *out* to every instance in it — so the default is an
    undirected adjacency. Pass ``undirected=False`` to keep only the literal
    reference direction (``src → dst``).
    """
    adjacency = {}
    for edge in build_edges(assets):
        adjacency.setdefault(edge.src, set()).add(edge.dst)
        if undirected:
            adjacency.setdefault(edge.dst, set()).add(edge.src)
    return adjacency
