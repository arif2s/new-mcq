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
_INTERVAL_SCALER = (REQUESTED_RETENTION ** -2.0 - 1) / 19  # Replaced (-1 / 0.5) with direct -2.0

# --- LOOKUP TABLES (LUTs) FOR CPU EFFICIENCY ---
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

# --- NEW ALGEBRAIC REDUCTION CONSTANTS ---
# Consolidates multiple static multiplications into single lookups
_AGAIN_MULTIPLIER = W[11] * _FACTOR_LAPSES
_RETENTION_MODIFIER = tuple(_EXP_W8 * _FACTOR_STABILITY * s_mult for s_mult in _S_MULTIPLIER)


def calculate_fsrs_next_interval(
    rating: int, state: int, difficulty: float, stability: float, reps: int, lapses: int,
    # Local Variable Binding (LOAD_FAST Optimization):
    # Binding globals as default arguments forces Python to access them as local variables in C,
    # bypassing the slower global dictionary lookup (LOAD_GLOBAL) on every function call.
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
    if reps == 0:
        d = _init_d[rating]
        s = _init_s[rating]
        next_state = _init_states[rating]
        new_lapses = _init_lapses[rating]
    else:
        new_d = difficulty - _diff_mod[rating]
        d = min(max(new_d * _mean_rev_mul + _mean_rev_base, 1.0), 10.0)

        if rating == 1:
            # Algebraic reduction: _again_mul replaces `W[11] * ... * _FACTOR_LAPSES`
            s = _again_mul * (d ** -_w12) * ((stability + 1) ** _w13 - 1)
            next_state = 3
            new_lapses = lapses + 1
        else:
            # Algebraic reduction: _ret_mod[rating] replaces `_EXP_W8 * ... * _FACTOR_STABILITY * _S_MULTIPLIER[rating]`
            s = stability * (1 + (11 - d) * (stability ** -_w9) * _ret_mod[rating])
            next_state = 2
            new_lapses = lapses

    interval_days = max(1, round(s * _int_scaler))

    return {
        "state": next_state,
        "difficulty": round(d, 4),
        "stability": round(s, 4),
        "reps": reps + 1,
        "lapses": new_lapses,
        "interval_seconds": int(interval_days * 86400)
    }
