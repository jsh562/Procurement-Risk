"""The read-side arithmetic model-facing code may not reach."""

STALENESS_THRESHOLD_DAYS = 7


def is_stale(age_days: int) -> bool:
    return age_days > STALENESS_THRESHOLD_DAYS
