"""
core/scheduler.py - FSRS v4.5 spaced repetition scheduling algorithm.
Enforces zero VRAM usage, exact mathematical scaling (1.0x at 90% retention),
stability floor guards, and strict contract adherence.
"""

from __future__ import annotations

import math
import os
import time
from typing import Any, Dict, Optional

# Enforce Invariant 1: Zero VRAM for backend processes
os.environ["CUDA_VISIBLE_DEVICES"] = ""

# -----------------------------------------------------------------------------
# FSRS v4.5 Default Weights & Model Constants
# -----------------------------------------------------------------------------
W = (
    0.40255,
    1.18385,
    3.173,
    15.69105,
    7.1949,
    0.5345,
    1.4604,
    0.0046,
    1.54575,
    0.1192,
    1.01925,
    1.9395,
    0.11,
    0.29605,
    0.22695,
    0.56995,
    2.85535,
)
REQUESTED_RETENTION: float = 0.9

# Precomputed decay factor: at t = S, R = (1 + FACTOR)^(-0.5) = 0.9 => FACTOR = (0.9^-2 - 1) = 19/81
_FACTOR_FORGETTING: float = (REQUESTED_RETENTION ** -2.0) - 1.0
_INTERVAL_SCALER: float = (
    (REQUESTED_RETENTION ** -2.0) - 1.0
) / _FACTOR_FORGETTING  # Exactly 1.0 at R=0.9

# Initial review lookup tables: index 0 is unused dummy, 1=Again, 2=Hard, 3=Good, 4=Easy
_INIT_D = (
    0.0,
    min(max(W[4] - math.exp(W[5] * 0) + 1.0, 1.0), 10.0),  # G=1: 7.1949
    min(max(W[4] - math.exp(W[5] * 1) + 1.0, 1.0), 10.0),  # G=2: 6.4883
    min(max(W[4] - math.exp(W[5] * 2) + 1.0, 1.0), 10.0),  # G=3: 5.2824
    min(max(W[4] - math.exp(W[5] * 3) + 1.0, 1.0), 10.0),  # G=4: 3.2245
)
_INIT_S = (0.0, max(W[0], 0.1), max(W[1], 0.1), max(W[2], 0.1), max(W[3], 0.1))
_INIT_STATES = (0, 1, 2, 2, 2)  # 1=Learning, 2=Review

# Difficulty update constants: D' = D - w6 * (G - 3), with mean reversion towards D0(3) = 5.2824
_TARGET_D3: float = _INIT_D[3]
_MEAN_REV_BASE: float = W[7] * _TARGET_D3
_MEAN_REV_MULTIPLIER: float = 1.0 - W[7]
_DIFF_MOD = (0.0, 2.0 * W[6], 1.0 * W[6], 0.0, -1.0 * W[6])

# Stability update constants for recall (G in 2, 3, 4) and lapse (G=1)
_FACTOR_LAPSES: float = math.exp(W[14] * (1.0 - REQUESTED_RETENTION))
_AGAIN_MULTIPLIER: float = W[11] * _FACTOR_LAPSES

_FACTOR_STABILITY: float = math.exp((1.0 - REQUESTED_RETENTION) * W[10]) - 1.0
_EXP_W8: float = math.exp(W[8])
_S_MULTIPLIER = (0.0, 1.0, W[15], 1.0, W[16])
_RETENTION_MODIFIER = tuple(
    _EXP_W8 * _FACTOR_STABILITY * s_mult for s_mult in _S_MULTIPLIER
)


def map_fsrs_state_to_status(
    state: int, stability: float = 0.0, reps: int = 0
) -> str:
    """
    Maps FSRS numeric state and stability to UI status labels (Section 3):
    'new', 'learning', 'review', or 'mastered'.
    """
    state_int = int(state or 0)
    reps_int = int(reps or 0)
    s_val = float(stability or 0.0)

    if state_int == 0 or reps_int <= 0:
        return "new"
    if state_int in (1, 3):
        return "learning"
    if s_val >= 21.0 and reps_int >= 2:
        return "mastered"
    return "review"


def calculate_fsrs_next_interval(
    rating: int,
    state: int,
    difficulty: float,
    stability: float,
    reps: int,
    lapses: int,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Computes updated FSRS v4.5 metrics adhering to the Section 3 & Section 4 FSRS Stats Dict contract.

    Parameters:
    - rating: 1=Again, 2=Hard, 3=Good, 4=Easy
    - state: 0=New, 1=Learning, 2=Review, 3=Relearning
    - difficulty: float (1.0 - 10.0)
    - stability: float (days)
    - reps: total prior reviews
    - lapses: total prior lapses from review state
    - now: optional Unix timestamp for exact due_timestamp computation
    """
    try:
        grade = max(1, min(4, int(rating if rating is not None else 3)))
    except (ValueError, TypeError):
        grade = 3

    curr_state = max(0, int(state or 0))
    curr_d = float(difficulty or 0.0)
    curr_s = float(stability or 0.0)
    curr_reps = max(0, int(reps or 0))
    curr_lapses = max(0, int(lapses or 0))

    # Initial Review (New Card)
    if curr_reps == 0 or curr_state == 0:
        next_d = _INIT_D[grade]
        next_s = _INIT_S[grade]
        next_state = _INIT_STATES[grade]
        # Failing a new card on initial exposure is a learning step, not a lapse
        next_lapses = 0
    else:
        # Guard migrated cards that had reps > 0 but uninitialized difficulty (0.0)
        base_d = curr_d if 1.0 <= curr_d <= 10.0 else _TARGET_D3

        # Update difficulty with mean reversion towards D0(3)
        raw_d = base_d + _DIFF_MOD[grade]
        next_d = min(max(raw_d * _MEAN_REV_MULTIPLIER + _MEAN_REV_BASE, 1.0), 10.0)

        # Guard stability against zero-lock collapse (>= 0.1 floor)
        safe_s = max(curr_s, 0.1)

        # Rating 1: Again (Lapse / Relearning)
        if grade == 1:
            s_lapse = (
                _AGAIN_MULTIPLIER
                * (next_d ** -W[12])
                * (((safe_s + 1.0) ** W[13]) - 1.0)
            )
            next_s = max(round(s_lapse, 4), 0.1)
            if curr_state in (2, 3):
                next_state = 3  # Relearning
                next_lapses = curr_lapses + 1
            else:
                next_state = 1  # Still in initial Learning
                next_lapses = curr_lapses
        # Ratings 2, 3, 4: Hard, Good, Easy (Recall)
        else:
            s_recall = safe_s * (
                1.0
                + (11.0 - next_d) * (safe_s ** -W[9]) * _RETENTION_MODIFIER[grade]
            )
            next_s = max(round(s_recall, 4), 0.1)
            next_state = 2  # Review
            next_lapses = curr_lapses

    # Compute interval in days and seconds (I = S * 1.0 at 90% retention)
    interval_days = max(1, round(next_s * _INTERVAL_SCALER))
    interval_seconds = int(interval_days * 86400)
    new_reps = curr_reps + 1

    current_ts = float(now if now is not None else time.time())
    due_ts = current_ts + interval_seconds

    status_label = map_fsrs_state_to_status(next_state, next_s, new_reps)

    return {
        "state": int(next_state),
        "status": status_label,
        "difficulty": round(next_d, 4),
        "stability": next_s,
        "reps": int(new_reps),
        "lapses": int(next_lapses),
        "interval_days": int(interval_days),
        "interval_seconds": interval_seconds,
        "due_timestamp": due_ts,
    }
