"""The ranking arithmetic model-facing code may not reach."""

FUSION_CONSTANT = 60


def reciprocal_rank(rank: int) -> float:
    return 1.0 / (FUSION_CONSTANT + rank)
