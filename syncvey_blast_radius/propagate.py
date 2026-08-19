"""
propagate.py
------------
Step 3 of blast-radius: given the reference graph (step 2) and the set of
resources that actually drifted, walk outward to find everything the drift can
reach — the *blast radius* — ranked by how many hops away each asset is.

Multi-source breadth-first search over the (by default undirected) adjacency
from step 2: every drifted ``cloud_id`` starts at distance 0, and each other
asset gets the shortest hop-distance to the nearest drifted node. BFS, not
Dijkstra, because every edge is one hop here; severity-weighted decay (heapq)
is a natural step-4 refinement layered on top of this same graph.

Pure over the step-2 adjacency and asset records — no DB, no AWS — so it stays
exhaustively testable, like steps 1–2 and ``syncvey_drift_risk``.
"""

from collections import deque, namedtuple

from .graph import build_adjacency

# One asset caught in a drift's blast radius. ``asset`` is the source record
# (or ``None`` if a drifted id has no matching asset), ``hops`` is the shortest
# distance to the nearest drifted node, ``is_source`` marks the drifted assets.
ImpactedAsset = namedtuple('ImpactedAsset', ('cloud_id', 'asset', 'hops', 'is_source'))


def blast_radius(adjacency, sources, max_hops=None) -> dict:
    """
    Multi-source BFS over ``adjacency`` (``{node: set(neighbours)}``) from the
    drifted ``sources`` → ``{cloud_id: hops}``, the shortest hop-distance from
    the nearest source to every reachable node. Sources sit at distance 0.

    - ``sources`` may be a single ``cloud_id`` string or any iterable of them.
    - A source that isn't a node in the graph still reports at distance 0: a
      drifted asset with no wired edges is its own (singleton) blast radius.
    - ``max_hops`` caps traversal depth (``None`` = unbounded); nodes farther
      than the cap are simply never reached.
    """
    if isinstance(sources, str):
        sources = (sources,)

    dist = {}
    queue = deque()
    for s in sources:
        if s not in dist:
            dist[s] = 0
            queue.append(s)

    while queue:
        node = queue.popleft()
        d = dist[node]
        if max_hops is not None and d >= max_hops:
            continue
        for neighbour in adjacency.get(node, ()):
            if neighbour not in dist:
                dist[neighbour] = d + 1
                queue.append(neighbour)

    return dist


def blast_report(assets, drifted_ids, max_hops=None, undirected=True) -> list:
    """
    Full step-2→3 pipeline: build the reference graph from ``assets``,
    propagate from the ``drifted_ids``, and return the impacted assets ranked by
    proximity to the drift.

    Returns a list of :class:`ImpactedAsset` sorted by ``(hops, cloud_id)`` —
    drifted sources first (hops 0), then the widening rings of collateral. Each
    entry carries the originating asset record (joined back by ``cloud_id``) so
    callers can render names; a drifted id with no matching asset still appears
    with ``asset=None``.
    """
    assets = list(assets)
    by_id = {}
    for a in assets:
        cid = getattr(a, 'cloud_id', None)
        if cid and cid not in by_id:
            by_id[cid] = a

    adjacency = build_adjacency(assets, undirected=undirected)
    sources = set(drifted_ids)
    dist = blast_radius(adjacency, sources, max_hops=max_hops)

    report = [
        ImpactedAsset(
            cloud_id=cid,
            asset=by_id.get(cid),
            hops=hops,
            is_source=cid in sources,
        )
        for cid, hops in dist.items()
    ]
    report.sort(key=lambda item: (item.hops, item.cloud_id))
    return report
