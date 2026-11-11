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
DEFAULT_REQUESTED_RETENTION: float = 0.9
DEFAULT_MAX_INTERVAL_DAYS: int = 365
REQUESTED_RETENTION: float = DEFAULT_REQUESTED_RETENTION

# Precomputed decay factor: at t = S, R = (1 + FACTOR)^(-0.5) = 0.9 => FACTOR = (0.9^-2 - 1) = 19/81 (~0.2345679)
_FACTOR_FORGETTING: float = (0.9 ** -2.0) - 1.0
_DECAY_POWER: float = -0.5
_INTERVAL_SCALER: float = (
    (REQUESTED_RETENTION ** -2.0) - 1.0
) / _FACTOR_FORGETTING  # Exactly 1.0 at R=0.9


def compute_retrievability(elapsed_days: float, stability: float) -> float:
    """Computes FSRS power-law retrievability R(t, S) in [0.0, 1.0]."""
    if elapsed_days <= 0.0:
        return 1.0
    safe_s = max(stability, 0.1)
    return float((1.0 + _FACTOR_FORGETTING * (elapsed_days / safe_s)) ** _DECAY_POWER)

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
    state: int | float | str,
    stability: int | float | str = 0.0,
    reps: int | float | str = 0,
) -> str:
    """
    Deterministically maps FSRS numeric states to UI lifecycle labels:
    0: 'new', 1: 'learning', 2: 'review' (or 'mastered'), 3: 'relearning'.
    """
    try:
        state_int = int(state)
    except (ValueError, TypeError):
        state_int = 0

    try:
        reps_int = int(reps)
    except (ValueError, TypeError):
        reps_int = 0

    try:
        s_val = float(stability)
        if math.isnan(s_val) or math.isinf(s_val):
            s_val = 0.0
    except (ValueError, TypeError):
        s_val = 0.0

    # Strict FSRS State Machine Invariants
    if state_int == 0:
        return "new"
    if state_int in (1, 3):
        return "learning"
    if state_int == 2:
        if s_val >= 21.0 and reps_int >= 2:
            return "mastered"
        return "review"

    return "new" if reps_int <= 0 else "review"


from dataclasses import dataclass
import json


@dataclass(frozen=True)
class FSRSConfig:
    requested_retention: float
    max_interval: int
    easy_bonus: float = 1.3
    hard_factor: float = 0.8


class CachedFSRSConfigProvider:
    """Thread-safe in-memory cache for spaced repetition parameters."""

    _cached_config: Optional[FSRSConfig] = None
    _last_fetched: float = 0.0
    _CACHE_TTL_SECONDS: float = 300.0

    @classmethod
    def get_parameters(cls) -> FSRSConfig:
        now = time.time()
        if cls._cached_config is not None and (now - cls._last_fetched < cls._CACHE_TTL_SECONDS):
            return cls._cached_config

        from core.database import get_db_connection
        retention = DEFAULT_REQUESTED_RETENTION
        max_interval = DEFAULT_MAX_INTERVAL_DAYS
        easy_bonus = 1.3
        hard_factor = 0.8

        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT value FROM user_state WHERE key = 'spaced_repetition_config' LIMIT 1"
                )
                row = cursor.fetchone()
                if row and row["value"]:
                    raw_val = row["value"]
                    cfg = json.loads(raw_val) if isinstance(raw_val, str) else raw_val
                    if isinstance(cfg, dict):
                        retention = float(cfg.get("requested_retention", retention))
                        max_interval = int(cfg.get("maximum_interval", cfg.get("max_interval_days", max_interval)))
                        easy_bonus = float(cfg.get("easy_bonus", easy_bonus))
                        hard_factor = float(cfg.get("hard_factor", hard_factor))
        except Exception:
            pass

        cls._cached_config = FSRSConfig(
            requested_retention=max(0.50, min(0.99, retention)),
            max_interval=max(1, min(36500, max_interval)),
            easy_bonus=easy_bonus,
            hard_factor=hard_factor,
        )
        cls._last_fetched = now
        return cls._cached_config

    @classmethod
    def invalidate(cls) -> None:
        cls._cached_config = None
        cls._last_fetched = 0.0


def get_fsrs_parameters() -> Dict[str, Any]:
    """Reads user-configured FSRS parameters from SQLite user_state via thread-safe in-memory cache."""
    cfg = CachedFSRSConfigProvider.get_parameters()
    return {
        "requested_retention": cfg.requested_retention,
        "max_interval_days": cfg.max_interval,
        "easy_bonus": cfg.easy_bonus,
        "hard_factor": cfg.hard_factor,
    }


def compute_next_fsrs_state(
    curr_state: int,
    curr_s: float,
    curr_d: float,
    curr_lapses: int,
    grade: int,
    elapsed_days: float = 0.0,
    requested_retention: Optional[float] = None,
    max_interval_days: Optional[int] = None,
    W: Tuple[float, ...] = (
        0.4, 0.6, 2.4, 5.8, 4.93, 0.94, 0.86, 0.01,
        1.49, 0.14, 0.94, 2.18, 0.05, 0.34, 1.26, 0.29, 2.61,
    ),
) -> Dict[str, float | int]:
    """Computes next FSRS stability, difficulty, state, and interval using elapsed-time retrievability."""
    safe_s = max(float(curr_s or 0.1), 0.1)
    next_d = min(10.0, max(1.0, float(curr_d or 5.0)))

    if grade == 1:
        r_now = compute_retrievability(elapsed_days, safe_s)
        s_forget = (
            W[11]
            * (next_d ** -W[12])
            * (((safe_s + 1.0) ** W[13]) - 1.0)
            * math.exp(W[14] * (1.0 - r_now))
        )
        next_s = max(round(min(safe_s, s_forget), 4), 0.1)
        if curr_state in (2, 3):
            next_state = 3  # Relearning
            next_lapses = curr_lapses + 1
        else:
            next_state = 1  # Initial Learning
            next_lapses = curr_lapses
    else:
        # Use dynamic retrievability for active review/relearning cards; default to 0.90 on initial graduation
        r_now = compute_retrievability(elapsed_days, safe_s) if curr_state in (2, 3) else 0.90
        grade_factor = W[15] if grade == 2 else (W[16] if grade == 4 else 1.0)
        s_inc = (
            math.exp(W[8])
            * (11.0 - next_d)
            * (safe_s ** -W[9])
            * (math.exp(W[10] * (1.0 - r_now)) - 1.0)
            * grade_factor
        )
        next_s = max(round(safe_s * (1.0 + s_inc), 4), 0.1)
        next_state = 2  # Review
        next_lapses = curr_lapses

    if requested_retention is None or max_interval_days is None:
        params = get_fsrs_parameters()
        target_r = float(requested_retention if requested_retention is not None else params["requested_retention"])
        max_days = int(max_interval_days if max_interval_days is not None else params["max_interval_days"])
    else:
        target_r = float(requested_retention)
        max_days = int(max_interval_days)

    target_r = max(0.70, min(0.99, target_r))
    factor_r = (target_r ** -2.0) - 1.0
    scaler = factor_r / _FACTOR_FORGETTING

    if grade == 1:
        interval_days = 0
        interval_seconds = 0
    else:
        raw_days = next_s * scaler
        interval_days = max(1, min(max_days, int(round(raw_days))))
        interval_seconds = interval_days * 86400

    return {
        "stability": next_s,
        "difficulty": next_d,
        "state": next_state,
        "lapses": next_lapses,
        "interval_days": interval_days,
        "interval_seconds": interval_seconds,
    }


def calculate_fsrs_next_interval(
    rating: int,
    state: int,
    difficulty: float,
    stability: float,
    reps: int,
    lapses: int,
    now: Optional[float] = None,
    requested_retention: Optional[float] = None,
    max_interval_days: Optional[int] = None,
    elapsed_days: Optional[float] = None,
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
    - elapsed_days: optional days since last review (defaults to stability if None for review state)
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

    # Determine elapsed days
    actual_elapsed = float(elapsed_days) if elapsed_days is not None else (curr_s if curr_state in (2, 3) else 0.0)

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

        # Compute dynamic retrievability
        r_now = compute_retrievability(actual_elapsed, safe_s) if curr_state in (2, 3) else 0.90

        # Rating 1: Again (Lapse / Relearning)
        if grade == 1:
            s_lapse = (
                W[11]
                * (next_d ** -W[12])
                * (((safe_s + 1.0) ** W[13]) - 1.0)
                * math.exp(W[14] * (1.0 - r_now))
            )
            next_s = max(round(min(safe_s, s_lapse), 4), 0.1)
            if curr_state in (2, 3):
                next_state = 3  # Relearning
                next_lapses = curr_lapses + 1
            else:
                next_state = 1  # Still in initial Learning
                next_lapses = curr_lapses
        # Ratings 2, 3, 4: Hard, Good, Easy (Recall)
        else:
            grade_factor = W[15] if grade == 2 else (W[16] if grade == 4 else 1.0)
            s_inc = (
                math.exp(W[8])
                * (11.0 - next_d)
                * (safe_s ** -W[9])
                * (math.exp(W[10] * (1.0 - r_now)) - 1.0)
                * grade_factor
            )
            next_s = max(round(safe_s * (1.0 + s_inc), 4), 0.1)
            next_state = 2  # Review
            next_lapses = curr_lapses

    # Resolve user-tweaked FSRS parameters conditionally
    easy_bonus = 1.3
    hard_factor = 0.8
    if requested_retention is None or max_interval_days is None:
        params = get_fsrs_parameters()
        target_r = float(requested_retention if requested_retention is not None else params["requested_retention"])
        max_days = int(max_interval_days if max_interval_days is not None else params["max_interval_days"])
        easy_bonus = float(params.get("easy_bonus", easy_bonus))
        hard_factor = float(params.get("hard_factor", hard_factor))
    else:
        target_r = float(requested_retention)
        max_days = int(max_interval_days)

    target_r = max(0.70, min(0.99, target_r))

    # Decay factor formula: I = S * (R^(-1/decay) - 1) / (0.9^(-1/decay) - 1) with standard power -2.0
    factor_r = (target_r ** -2.0) - 1.0
    scaler = factor_r / _FACTOR_FORGETTING

    # Compute interval in days and seconds
    # When grade == 1 (Again), card enters learning/relearning and is due immediately (or next set)
    if grade == 1:
        interval_days = 0
        interval_seconds = 0
    else:
        raw_interval = next_s * scaler
        if grade == 4:
            raw_interval *= easy_bonus
        elif grade == 2:
            raw_interval *= hard_factor
        interval_days = max(1, min(max_days, int(round(raw_interval))))
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


# --- Phase 5 FSRS-4.5 / FSRS-5 Spaced Repetition & Response-Time Modulation ---
import math as _math
from typing import Any as _Any


def compute_fsrs_retrievability(elapsed_days: float, stability: float) -> float:
    """Compute FSRS power-law forgetting curve retrievability R(t, S) in [0.0, 1.0]."""
    if stability <= 0.0:
        return 0.0
    if elapsed_days <= 0.0:
        return 1.0
    # FSRS-4.5 / FSRS-5 power forgetting curve: R(t, S) = (1 + (19/81) * t / S) ** (-0.5)
    factor = 19.0 / 81.0
    decay = -0.5
    return float(max(0.0, min(1.0, (1.0 + factor * (elapsed_days / stability)) ** decay)))


def update_fsrs_card_state(
    card_state: dict[str, _Any] | None,
    rating: int,
    elapsed_days: float = 0.0,
    response_time_ms: int | None = None,
    slow_guess_threshold_ms: int = 45000,
    desired_retention: float = 0.9,
) -> dict[str, _Any]:
    """
    Update FSRS memory state (stability, difficulty, retrievability, interval_days, reps, lapses).
    rating: 1=Again, 2=Hard, 3=Good, 4=Easy.
    If rating >= 3 but response_time_ms exceeds slow_guess_threshold_ms, downgrade effective rating by 1
    to penalize slow lucky guesses.
    """
    state = dict(card_state or {})
    rating = max(1, min(4, int(rating)))

    # Modulate rating by response latency for slow answers
    effective_rating = rating
    if response_time_ms is not None and response_time_ms > slow_guess_threshold_ms and effective_rating > 2:
        effective_rating -= 1

    reps = int(state.get("reps", 0) or 0)
    lapses = int(state.get("lapses", 0) or 0)
    prev_s = float(state.get("stability", 0.0) or 0.0)
    prev_d = float(state.get("difficulty", 5.0) or 5.0)

    if reps == 0 or prev_s <= 0.0:
        # Initial FSRS stability & difficulty seeds per rating (1..4)
        init_s_map = {1: 0.4, 2: 1.2, 3: 3.1, 4: 7.5}
        init_d_map = {1: 7.2, 2: 6.1, 3: 4.9, 4: 3.5}
        new_s = init_s_map[effective_rating]
        new_d = init_d_map[effective_rating]
        reps = 1
        if effective_rating == 1:
            lapses += 1
    else:
        r = compute_fsrs_retrievability(max(0.0, elapsed_days), prev_s)
        # Update Difficulty D in [1.0, 10.0] with mean reversion toward 5.0
        delta_d = -0.8 * (effective_rating - 3)
        new_d = prev_d + delta_d
        new_d = 0.9 * new_d + 0.1 * 5.0
        new_d = max(1.0, min(10.0, new_d))

        if effective_rating == 1:
            lapses += 1
            # Post-lapse stability contraction
            new_s = max(0.2, prev_s * 0.28 * ((new_d / 10.0) ** -0.2))
        else:
            reps += 1
            hard_penalty = 0.75 if effective_rating == 2 else 1.0
            easy_bonus = 1.35 if effective_rating == 4 else 1.0
            growth = 1.0 + _math.exp(1.1) * (11.0 - new_d) * (prev_s ** -0.15) * (_math.exp(0.8 * (1.0 - r)) - 1.0)
            new_s = max(prev_s + 0.1, prev_s * (1.0 + (growth - 1.0) * hard_penalty * easy_bonus))

    # Calculate next interval in days for target desired_retention
    # Inverse of R(t, S): I(r, S) = (81/19) * S * (r ** (-2) - 1)
    target_r = max(0.7, min(0.97, desired_retention))
    raw_interval = (81.0 / 19.0) * new_s * ((target_r ** -2.0) - 1.0)
    interval_days = max(1, int(round(raw_interval))) if effective_rating > 1 else 1

    state.update(
        {
            "stability": round(new_s, 4),
            "difficulty": round(new_d, 4),
            "retrievability": round(compute_fsrs_retrievability(0.0, new_s), 4),
            "interval_days": interval_days,
            "reps": reps,
            "lapses": lapses,
            "last_rating": rating,
            "effective_rating": effective_rating,
        }
    )
    return state


__all__ = [
    "W",
    "compute_retrievability",
    "compute_next_fsrs_state",
    "calculate_fsrs_next_interval",
    "get_fsrs_parameters",
    "map_fsrs_state_to_status",
    "compute_fsrs_retrievability",
    "update_fsrs_card_state",
    "CachedFSRSConfigProvider",
    "FSRSConfig",
]
