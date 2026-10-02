import math
from typing import Dict, Any

# FSRS v4.5 Default Weights
# Converted to a tuple for faster iteration and immutability in memory
W = (
    0.40255, 1.18385, 3.173, 15.69105, 7.1949, 0.5345, 1.4604, 0.0046,
    1.54575, 0.1192, 1.01925, 1.9395, 0.11, 0.29605, 0.22695, 0.56995, 2.85535
)
REQUESTED_RETENTION = 0.9

# Pre-computed mathematical constants
_FACTOR_LAPSES = math.exp(W[14] * (1 - REQUESTED_RETENTION))
_FACTOR_STABILITY = math.exp((1 - REQUESTED_RETENTION) * W[10]) - 1
_EXP_W8 = math.exp(W[8])
_INTERVAL_SCALER = (REQUESTED_RETENTION ** (-1 / 0.5) - 1) / 19

# --- LOOKUP TABLES (LUTs) FOR CPU EFFICIENCY ---
# 1-indexed tuples to map directly to ratings (1=Again, 2=Hard, 3=Good, 4=Easy)
# Index 0 is a padding zero to align the rating integers directly with the indices.

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

# Review modifiers pre-calculated
_DIFF_MOD = (0.0, W[6] * -2, W[6] * -1, 0.0, W[6] * 1)
_MEAN_REV_BASE = W[7] * W[4]
_MEAN_REV_MULTIPLIER = 1 - W[7]
_S_MULTIPLIER = (0.0, 1.0, W[15], 1.0, W[16])


def calculate_fsrs_next_interval(rating: int, state: int, difficulty: float, stability: float, reps: int, lapses: int) -> Dict[str, Any]:
    """
    Computes updated FSRS v4.5 metrics.
    rating: 1=Again, 2=Hard, 3=Good, 4=Easy
    state: 0=New, 1=Learning, 2=Review, 3=Relearning
    """
    if reps == 0:
        d = _INIT_D[rating]
        s = _INIT_S[rating]
        next_state = _INIT_STATES[rating]
        new_lapses = _INIT_LAPSES[rating]
    else:
        # Reduced arithmetic operations by combining the mean reversion formula
        new_d = difficulty - _DIFF_MOD[rating]
        d = min(max(new_d * _MEAN_REV_MULTIPLIER + _MEAN_REV_BASE, 1.0), 10.0)

        if rating == 1:
            # Replaced math.pow with the ** operator for faster execution on native C-level
            s = W[11] * (d ** -W[12]) * ((stability + 1) ** W[13] - 1) * _FACTOR_LAPSES
            next_state = 3
            new_lapses = lapses + 1
        else:
            s = stability * (1 + _EXP_W8 * (11 - d) * (stability ** -W[9]) * _FACTOR_STABILITY * _S_MULTIPLIER[rating])
            next_state = 2
            new_lapses = lapses

    interval_days = max(1, round(s * _INTERVAL_SCALER))

    return {
        "state": next_state,
        "difficulty": round(d, 4),
        "stability": round(s, 4),
        "reps": reps + 1,
        "lapses": new_lapses,
        "interval_seconds": int(interval_days * 86400)
    }
