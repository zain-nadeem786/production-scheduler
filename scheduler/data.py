"""Synthetic demand for a small FMCG-style production line.

All numbers are made up. They are shaped to look like a real line: a few fast
movers, a slow mover, weekday/weekend patterns and some noise.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

PRODUCTS = {
    # name: (average daily demand in units, line rate in units per hour)
    "Product A": (1200, 180),
    "Product B": (800, 160),
    "Product C": (450, 140),
    "Product D": (150, 120),
}

WEEKLY_SHAPE = np.array([1.05, 1.0, 1.0, 1.05, 1.15, 0.9, 0.85])  # Mon..Sun


def sample_demand(days: int = 14, seed: int = 7, scale: float = 1.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    data = {}
    for name, (avg, _) in PRODUCTS.items():
        shape = np.resize(WEEKLY_SHAPE, days)
        noise = rng.normal(1.0, 0.12, days).clip(0.6, 1.4)
        data[name] = np.round(avg * scale * shape * noise)
    df = pd.DataFrame(data)
    df.index.name = "day"
    return df


def default_rates() -> dict[str, float]:
    return {name: rate for name, (_, rate) in PRODUCTS.items()}


def default_initial_stock(days_of_cover: float = 1.0) -> dict[str, float]:
    return {name: avg * days_of_cover for name, (avg, _) in PRODUCTS.items()}
