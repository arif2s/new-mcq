import math
from typing import Dict, Any

# FSRS v4.5 Default Weights (W0 - W16)
W = [0.40255, 1.18385, 3.173, 15.69105, 7.1949, 0.5345, 1.4604, 0.0046, 1.54575, 0.1192, 1.01925, 1.9395, 0.11, 0.29605, 0.22695, 0.56995, 2.85535]
REQUESTED_RETENTION = 0.9

# Pre-computed mathematical constants for CPU efficiency
_FACTOR_LAPSES = math.exp(W[14] * (1 - REQUESTED_RETENTION))
_FACTOR_STABILITY = math.exp((1 - REQUESTED_RETENTION) * W[10]) - 1
_EXP_W8 = math.exp(W[8])
_INTERVAL_SCALER = (math.pow(REQUESTED_RETENTION, -1 / 0.5) - 1) / 19

def calculate_fsrs_next_interval(rating: int, state: int, difficulty: float, stability: float, reps: int, lapses: int) -> Dict[str, Any]:
    """
    Computes updated FSRS v4.5 metrics.
    rating: 1=Again, 2=Hard, 3=Good, 4=Easy
    state: 0=New, 1=Learning, 2=Review, 3=Relearning
    """
    if reps == 0:
        # Initial review
        init_d = W[4] - math.exp(W[5] * (rating - 1)) + 1
        d = min(max(init_d, 1.0), 10.0)
        s = max(W[rating - 1], 0.1)
        next_state = 1 if rating == 1 else 2
        new_lapses = 1 if rating == 1 else 0
    else:
        # Subsequent reviews
        new_d = difficulty - W[6] * (rating - 3)
        mean_reversion = W[7] * (W[4] - new_d)
        d = min(max(new_d + mean_reversion, 1.0), 10.0)

        if rating == 1:
            s = W[11] * math.pow(d, -W[12]) * (math.pow(stability + 1, W[13]) - 1) * _FACTOR_LAPSES
            next_state = 3
            new_lapses = lapses + 1
        else:
            hard_penalty = W[15] if rating == 2 else 1.0
            easy_bonus = W[16] if rating == 4 else 1.0
            s = stability * (1 + _EXP_W8 * (11 - d) * math.pow(stability, -W[9]) * _FACTOR_STABILITY * hard_penalty * easy_bonus)
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
