import math
from typing import Dict, Any

# FSRS v4.5 Default Weights
W = (
    0.40255, 1.18385, 3.173, 15.69105, 7.1949, 0.5345, 1.4604, 0.0046,
    1.54575, 0.1192, 1.01925, 1.9395, 0.11, 0.29605, 0.22695, 0.56995, 2.85535
)
REQUESTED_RETENTION = 0.9

# Pre-computed mathematical constants
_FACTOR_LAPSES = math.exp(W[14] * (1 - REQUESTED_RETENTION))
_FACTOR_STABILITY = math.exp((1 - REQUESTED_RETENTION) * W[10]) - 1
_EXP_W8 = math.exp(W[8])
_INTERVAL_SCALER = (REQUESTED_RETENTION ** -2.0 - 1) / 19

# Lookup Tables (LUTs) for CPU efficiency
_INIT_STATES = (0, 1, 2, 2, 2)
_INIT_LAPSES = (0, 1, 0, 0, 0)
_INIT_D = (
    0.0,
    min(max(W[4] - math.exp(W[5] * 0) + 1, 1.0), 10.0),
    min(max(W[4] - math.exp(W[5] * 1) + 1, 1.0), 10.0),
    min(max(W[4] - math.exp(W[5] * 2) + 1, 1.0), 10.0),
    min(max(W[4] - math.exp(W[5] * 3) + 1, 1.0), 10.0)
)
_INIT_S = (0.0, max(W[0], 0.1), max(W[1], 0.1), max(W[2], 0.1), max(W[3], 0.1))

_DIFF_MOD = (0.0, W[6] * -2, W[6] * -1, 0.0, W[6] * 1)
_MEAN_REV_BASE = W[7] * W[4]
_MEAN_REV_MULTIPLIER = 1 - W[7]
_S_MULTIPLIER = (0.0, 1.0, W[15], 1.0, W[16])

_AGAIN_MULTIPLIER = W[11] * _FACTOR_LAPSES
_RETENTION_MODIFIER = tuple(_EXP_W8 * _FACTOR_STABILITY * s_mult for s_mult in _S_MULTIPLIER)


def map_fsrs_state_to_status(state: int, stability: float = 0.0, reps: int = 0) -> str:
    """
    Maps FSRS numeric state and stability to UI status labels:
    'new', 'learning', 'review', or 'mastered'.
    """
    if state == 0 or reps == 0:
        return "new"
    if state in (1, 3):
        return "learning"
    if stability >= 21.0 and reps >= 2:
        return "mastered"
    return "review"


def calculate_fsrs_next_interval(
    rating: int, state: int, difficulty: float, stability: float, reps: int, lapses: int,
    _init_states=_INIT_STATES, _init_lapses=_INIT_LAPSES,
    _init_d=_INIT_D, _init_s=_INIT_S, _diff_mod=_DIFF_MOD,
    _mean_rev_base=_MEAN_REV_BASE, _mean_rev_mul=_MEAN_REV_MULTIPLIER,
    _again_mul=_AGAIN_MULTIPLIER, _ret_mod=_RETENTION_MODIFIER,
    _int_scaler=_INTERVAL_SCALER, _w9=W[9], _w12=W[12], _w13=W[13]
) -> Dict[str, Any]:
    """
    Computes updated FSRS v4.5 metrics.
    rating: 1=Again, 2=Hard, 3=Good, 4=Easy
    state: 0=New, 1=Learning, 2=Review, 3=Relearning
    """
    rating = max(1, min(4, int(rating)))
    state = int(state or 0)
    difficulty = float(difficulty or 0.0)
    stability = float(stability or 0.0)
    reps = int(reps or 0)
    lapses = int(lapses or 0)

    if reps == 0:
        d = _init_d[rating]
        s = _init_s[rating]
        next_state = _init_states[rating]
        new_lapses = _init_lapses[rating]
    else:
        new_d = difficulty - _diff_mod[rating]
        d = min(max(new_d * _mean_rev_mul + _mean_rev_base, 1.0), 10.0)

        if rating == 1:
            s = _again_mul * (d ** -_w12) * ((stability + 1) ** _w13 - 1)
            next_state = 3
            new_lapses = lapses + 1
        else:
            s = stability * (1 + (11 - d) * (max(stability, 0.1) ** -_w9) * _ret_mod[rating])
            next_state = 2
            new_lapses = lapses

    interval_days = max(1, round(s * _int_scaler))
    new_reps = reps + 1
    rounded_s = round(s, 4)

    return {
        "state": next_state,
        "status": map_fsrs_state_to_status(next_state, rounded_s, new_reps),
        "difficulty": round(d, 4),
        "stability": rounded_s,
        "reps": new_reps,
        "lapses": new_lapses,
        "interval_days": interval_days,
        "interval_seconds": int(interval_days * 86400)
    }
