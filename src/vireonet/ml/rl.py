"""Reinforcement learning: tabular Q-learning for rollout wave size.

Environment (simulated, advisory): a change touches N devices with a
per-device failure probability p. The agent picks a wave size; each wave
takes time; a failure stops the rollout and every device already changed
in that wave must be rolled back. Reward trades total time against blast
radius (devices hit by a failure). The learned policy is a recommendation
shown to operators; VireoNet's configured wave size is never changed by it.
"""

from __future__ import annotations

import random
from typing import Any

WAVE_SIZES = [1, 2, 4, 8, 16]
DEVICE_BUCKETS = [(1, 2), (3, 8), (9, 24), (25, 100)]
RISK_BUCKETS = [(0.0, 0.02), (0.02, 0.06), (0.06, 0.15), (0.15, 1.0)]


def _bucket(value: float, buckets: list[tuple[float, float]]) -> int:
    for i, (lo, hi) in enumerate(buckets):
        if lo <= value <= hi:
            return i
    return len(buckets) - 1


def simulate(
    devices: int,
    p_fail: float,
    wave: int,
    rnd: random.Random,
    minutes_per_wave: float = 6.0,
    blast_weight: float = 4.0,
) -> float:
    done, minutes = 0, 0.0
    while done < devices:
        size = min(wave, devices - done)
        minutes += minutes_per_wave + 0.4 * size
        failed = sum(1 for _ in range(size) if rnd.random() < p_fail)
        if failed:
            # Every device in the failed wave was changed and must roll back.
            return -(minutes + 2 * minutes_per_wave) - blast_weight * size
        done += size
    return -minutes


def _greedy(row: list[float], max_devices: int) -> int:
    """Best action; ties (within 0.5%) go to the smaller, safer wave, and
    waves larger than the change are pointless."""
    allowed = [i for i, w in enumerate(WAVE_SIZES) if w <= max_devices] or [0]
    best = max(row[i] for i in allowed)
    tol = abs(best) * 0.005 + 1e-9
    return min(i for i in allowed if row[i] >= best - tol)


def train_wave_policy(
    episodes: int = 60000,
    seed: int = 0,
    alpha: float = 0.0,
    epsilon: float = 0.2,
    blast_weight: float = 4.0,
) -> dict[str, Any]:
    """alpha=0 means sample-average updates (alpha = 1/visits), which converge
    to each action's mean reward in this one-step, stationary setting."""
    rnd = random.Random(seed)
    q = [[[0.0 for _ in WAVE_SIZES] for _ in RISK_BUCKETS] for _ in DEVICE_BUCKETS]
    visits = [[[0 for _ in WAVE_SIZES] for _ in RISK_BUCKETS] for _ in DEVICE_BUCKETS]
    curve = []
    window: list[float] = []
    for ep in range(episodes):
        d_b = rnd.randrange(len(DEVICE_BUCKETS))
        r_b = rnd.randrange(len(RISK_BUCKETS))
        devices = rnd.randint(*DEVICE_BUCKETS[d_b])
        p = rnd.uniform(*RISK_BUCKETS[r_b])
        row = q[d_b][r_b]
        a = (
            rnd.randrange(len(WAVE_SIZES))
            if rnd.random() < epsilon
            else _greedy(row, DEVICE_BUCKETS[d_b][1])
        )
        reward = simulate(devices, p, WAVE_SIZES[a], rnd, blast_weight=blast_weight)
        visits[d_b][r_b][a] += 1
        step = alpha or 1 / visits[d_b][r_b][a]
        row[a] += step * (reward - row[a])  # one-step episodes: Q <- Q + a(r - Q)
        window.append(reward)
        if (ep + 1) % max(1, episodes // 40) == 0:
            curve.append({"episode": ep + 1, "mean_reward": round(sum(window) / len(window), 2)})
            window = []
    policy = []
    for d_b, (dlo, dhi) in enumerate(DEVICE_BUCKETS):
        for r_b, (rlo, rhi) in enumerate(RISK_BUCKETS):
            row = q[d_b][r_b]
            best = _greedy(row, dhi)
            policy.append(
                {
                    "devices": f"{dlo}-{dhi}",
                    "risk": f"{rlo:.0%}-{rhi:.0%}",
                    "wave_size": WAVE_SIZES[best],
                    "q_values": {str(w): round(v, 2) for w, v in zip(WAVE_SIZES, row, strict=True)},
                }
            )
    return {
        "algorithm": "q_learning",
        "episodes": episodes,
        "alpha": alpha,
        "epsilon": epsilon,
        "blast_weight": blast_weight,
        "actions": WAVE_SIZES,
        "policy": policy,
        "learning_curve": curve,
        "_q": q,
    }


def recommend_wave(trained: dict[str, Any], devices: int, risk: float) -> int:
    d_b = _bucket(devices, [(float(a), float(b)) for a, b in DEVICE_BUCKETS])
    r_b = _bucket(risk, RISK_BUCKETS)
    row = trained["_q"][d_b][r_b]
    return min(devices, WAVE_SIZES[_greedy(row, DEVICE_BUCKETS[d_b][1])])
