"""Reciprocal rank fusion.

RRF combines dense and sparse result lists using only ranks, so the two scoring
scales (cosine similarity and BM25) never have to be normalised against each
other — which is what makes it robust when one retriever is much weaker than
the other, as with the built-in hashed embedder.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FusedHit:
    chunk_id: str
    score: float
    #: Rank this chunk achieved in each contributing retriever (1-based).
    ranks: dict[str, int] = field(default_factory=dict)
    #: Raw per-retriever scores, kept for debugging and for the UI's "why".
    raw_scores: dict[str, float] = field(default_factory=dict)

    @property
    def retrievers(self) -> list[str]:
        return sorted(self.ranks)


def reciprocal_rank_fusion(
    ranked_lists: dict[str, list[tuple[str, float]]],
    *,
    k: int = 60,
    weights: dict[str, float] | None = None,
    limit: int | None = None,
) -> list[FusedHit]:
    """Fuse named ranked lists of ``(chunk_id, score)`` pairs.

    ``k`` damps the influence of top ranks; 60 is the value from the original
    RRF paper and behaves well without tuning.
    """
    weights = weights or {}
    fused: dict[str, FusedHit] = {}

    for name, hits in ranked_lists.items():
        weight = weights.get(name, 1.0)
        for position, (chunk_id, score) in enumerate(hits, start=1):
            entry = fused.get(chunk_id)
            if entry is None:
                entry = FusedHit(chunk_id=chunk_id, score=0.0)
                fused[chunk_id] = entry
            entry.score += weight / (k + position)
            entry.ranks[name] = position
            entry.raw_scores[name] = score

    ordered = sorted(
        fused.values(),
        # Ties broken toward chunks found by more than one retriever.
        key=lambda hit: (hit.score, len(hit.ranks)),
        reverse=True,
    )
    return ordered[:limit] if limit else ordered
