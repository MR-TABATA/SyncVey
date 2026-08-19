"""
score.py
--------
Step 4 of blast-radius: turn the hop-distance rings from step 3 into a single
*impact score* per asset, so the blast radius can be ranked by how much it
actually matters — not just how close it sits to a drift.

Two refinements over the plain multi-source BFS of step 3:

1. **Severity weighting.** Each drifted source carries a weight — how bad *that*
   drift is (a security group opened to ``0.0.0.0/0`` outweighs a tag edit).
   ``syncvey_drift_risk`` already grades drift low/medium/high/critical;
   :func:`severity_weight` maps those grades onto numbers.

2. **Distance decay.** Impact fades with each hop: a resource one edge from a
   critical drift is more exposed than one five edges away. The score of a node
   reached from a source is ``weight × decay ** hops``.

Because a *far but severe* source can outrank a *near but mild* one, the nearest
source (step-3 BFS) is no longer the whole story: an asset's score is the best
(highest) score reachable from *any* source. Scores never grow along a path
(``0 < decay <= 1``), so the optimum settles greedily — a Dijkstra-style
best-first walk over a ``heapq`` rather than a plain BFS. This is exactly the
"severity-weighted decay (heapq)" that step 3 left as future work.

Pure over the step-2 adjacency, the weighted sources, and asset records — no DB,
no AWS — so it stays exhaustively testable, like the rest of the package.
"""

import heapq
from collections import namedtuple

from .graph import build_adjacency
from .propagate import blast_radius

# One asset scored by a drift's blast radius. ``asset`` is the source record (or
# ``None`` if a scored id has no matching asset), ``score`` is the best
# severity-weighted, distance-decayed impact reaching it from any source,
# ``hops`` is its shortest distance to the nearest source (step 3),
# ``is_source`` marks the drifted assets themselves.
ScoredAsset = namedtuple('ScoredAsset', ('cloud_id', 'asset', 'score', 'hops', 'is_source'))

# Grade → multiplicative weight. Doubling per level keeps a single critical
# drift firmly above any chain of lows, matching ``syncvey_drift_risk``'s
# low < medium < high < critical ordering without importing it.
_SEVERITY_WEIGHT = {'low': 1.0, 'medium': 2.0, 'high': 4.0, 'critical': 8.0}


def severity_weight(severity, default=1.0) -> float:
    """
    Map a ``syncvey_drift_risk`` severity grade to a numeric source weight.
    Unknown / ``None`` grades fall back to ``default`` (a plain low-ish source).
    """
    return _SEVERITY_WEIGHT.get(str(severity).lower(), default)


def impact_scores(adjacency, weighted_sources, decay=0.5, max_hops=None) -> dict:
    """
    Best-first propagation of severity-weighted, distance-decayed impact over
    ``adjacency`` (``{node: set(neighbours)}``) → ``{cloud_id: score}``.

    - ``weighted_sources`` is ``{cloud_id: weight}``: the drifted assets and how
      bad each one's drift is. Sources score at their own weight (hops 0).
    - Each hop multiplies the score by ``decay`` (``0 < decay <= 1``), so impact
      fades with distance. A node's score is the highest reachable from *any*
      source, which need not be the nearest one.
    - A source that isn't a node in the graph still scores at its weight: a
      drifted asset with no wired edges is its own singleton blast radius.
    - ``max_hops`` caps traversal depth (``None`` = unbounded).

    Because scores are non-increasing along every path, the first time a node is
    popped from the max-heap it is settled with its optimal score (Dijkstra).
    """
    if not 0 < decay <= 1:
        raise ValueError('decay must be in (0, 1], got %r' % (decay,))

    best = {}
    # heapq is a min-heap; negate the score so the largest pops first. The hops
    # tie-breaker keeps ordering total (and deterministic) without touching the
    # settle logic, which relies only on score.
    heap = [(-float(w), 0, cid) for cid, w in weighted_sources.items()]
    heapq.heapify(heap)

    while heap:
        neg_score, hops, node = heapq.heappop(heap)
        if node in best:
            continue  # already settled with an equal-or-better score
        best[node] = -neg_score

        if max_hops is not None and hops >= max_hops:
            continue
        child_score = -neg_score * decay
        child_hops = hops + 1
        for neighbour in adjacency.get(node, ()):
            if neighbour not in best:
                heapq.heappush(heap, (-child_score, child_hops, neighbour))

    return best


def _scored_assets(assets, weighted_sources, decay, max_hops, undirected) -> list:
    """Shared core of :func:`impact_report` / :func:`top_impacts`: build the
    graph, score it, and join each scored id back to its asset record. Returns
    an *unsorted* list of :class:`ScoredAsset`."""
    assets = list(assets)
    by_id = {}
    for a in assets:
        cid = getattr(a, 'cloud_id', None)
        if cid and cid not in by_id:
            by_id[cid] = a

    adjacency = build_adjacency(assets, undirected=undirected)
    sources = dict(weighted_sources)
    scores = impact_scores(adjacency, sources, decay=decay, max_hops=max_hops)
    # Reuse step 3 for the hop-distance column; same graph, same source set, so
    # every scored node has a hop entry (0 for a source or an off-graph drift).
    hops = blast_radius(adjacency, sources.keys(), max_hops=max_hops)

    return [
        ScoredAsset(
            cloud_id=cid,
            asset=by_id.get(cid),
            score=score,
            hops=hops.get(cid, 0),
            is_source=cid in sources,
        )
        for cid, score in scores.items()
    ]


def impact_report(assets, weighted_sources, decay=0.5, max_hops=None,
                  undirected=True) -> list:
    """
    Full step-2→4 pipeline: build the reference graph from ``assets``, propagate
    severity-weighted impact from the ``weighted_sources`` (``{cloud_id:
    weight}``), and return every impacted asset ranked by impact.

    Returns a list of :class:`ScoredAsset` sorted by ``(-score, hops,
    cloud_id)`` — hardest-hit first, then nearer, then by id for determinism.
    Each entry carries the originating asset record (joined back by
    ``cloud_id``) so callers can render names; a scored id with no matching
    asset still appears with ``asset=None``.
    """
    report = _scored_assets(assets, weighted_sources, decay, max_hops, undirected)
    report.sort(key=lambda item: (-item.score, item.hops, item.cloud_id))
    return report


def top_impacts(assets, weighted_sources, n, decay=0.5, max_hops=None,
                undirected=True) -> list:
    """
    The ``n`` hardest-hit assets, via ``heapq.nlargest`` — surfaces the top of
    the blast radius without a full sort of every reachable node, which is the
    whole point when a single hub drift can splash across a large environment.

    Equal-impact ties break toward fewer hops; the returned list is in
    descending-impact order.
    """
    scored = _scored_assets(assets, weighted_sources, decay, max_hops, undirected)
    return heapq.nlargest(n, scored, key=lambda i: (i.score, -i.hops))
