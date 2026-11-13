"""Capacitated lot-sizing model for a single production line.

The model decides, for every product and every day, whether to set the line
up for that product and how much to make. It trades off three costs:

* changeovers (setup time eats capacity, and each setup has a cost)
* holding inventory (cash, space, shelf life)
* backorders (demand that cannot be met on time)

Two objectives are supported so the "utilization trap" can be shown side by side:

* ``"total_cost"``: minimise changeover + holding + backorder cost
* ``"utilization"``: minimise changeovers (maximise productive hours),
  ignoring what that does to inventory, which is what a plant KPI rewards
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
import pulp


@dataclass
class LineSettings:
    hours_per_day: float = 22.0          # available hours per day
    setup_hours: float = 2.0             # hours lost per changeover
    setup_cost: float = 150.0            # cost per changeover
    holding_cost: float = 0.10           # cost per unit per day in stock
    backorder_cost: float = 5.0          # cost per unit per day late
    rates: dict[str, float] = field(default_factory=dict)  # units per hour by product
    initial_stock: dict[str, float] = field(default_factory=dict)


@dataclass
class ScheduleResult:
    status: str
    objective: str
    plan: pd.DataFrame          # one row per product and day
    kpis: dict[str, float]


def solve(demand: pd.DataFrame, settings: LineSettings, objective: str = "total_cost",
          time_limit: int = 8) -> ScheduleResult:
    """Solve the lot-sizing problem.

    ``demand`` is a wide table: index = day number (0..T-1), columns = products.
    """
    products = list(demand.columns)
    days = list(range(len(demand)))
    d = {(p, t): float(demand.iloc[t][p]) for p in products for t in days}
    rate = {p: settings.rates.get(p, 100.0) for p in products}
    i0 = {p: settings.initial_stock.get(p, 0.0) for p in products}
    cap = settings.hours_per_day
    # most that could still be needed from day t on: future demand + the ending stock target
    remaining = {(p, t): sum(d[p, k] for k in days if k >= t) + (0.0 if t == 0 else i0[p])
                 for p in products for t in days}
    remaining = {k: max(v, 1.0) for k, v in remaining.items()}

    prob = pulp.LpProblem("production_schedule", pulp.LpMinimize)
    x = pulp.LpVariable.dicts("make", (products, days), lowBound=0)
    y = pulp.LpVariable.dicts("setup", (products, days), cat="Binary")
    inv = pulp.LpVariable.dicts("stock", (products, days), lowBound=0)
    back = pulp.LpVariable.dicts("backorder", (products, days), lowBound=0)
    # setup carryover: w[p][t] = 1 if the line starts day t already set up for p
    w = pulp.LpVariable.dicts("carry", (products, days), cat="Binary")
    # v[t] = 1 if the line runs a single product all day with no changeover
    v = pulp.LpVariable.dicts("no_change", days, cat="Binary")

    setups = pulp.lpSum(y[p][t] for p in products for t in days)
    holding = pulp.lpSum(inv[p][t] for p in products for t in days)
    backlog = pulp.lpSum(back[p][t] for p in products for t in days)

    if objective == "total_cost":
        prob += (settings.setup_cost * setups
                 + settings.holding_cost * holding
                 + settings.backorder_cost * backlog)
    elif objective == "utilization":
        # Meet demand first, then use as few changeovers as possible.
        # Inventory gets only a tiny tie-break weight: the plant KPI does not see it.
        prob += 1e6 * backlog + 1e3 * setups + 1e-4 * holding
    else:
        raise ValueError(f"unknown objective: {objective}")

    for p in products:
        for t in days:
            prev_inv = i0[p] if t == 0 else inv[p][t - 1]
            prev_back = 0 if t == 0 else back[p][t - 1]
            # flow balance: what we had + what we make - what we owe = what we end with
            prob += prev_inv - prev_back + x[p][t] - d[p, t] == inv[p][t] - back[p][t]
            # can only produce if the line is set up for p today or carried over from yesterday
            # tight big-M: never make more than the line can run or than is still needed
            big_m = min(rate[p] * (cap - settings.setup_hours), remaining[p, t])
            prob += x[p][t] <= big_m * (y[p][t] + w[p][t])
            if t >= 1:
                # a setup state can only be carried in if p was running yesterday
                prob += w[p][t] <= y[p][t - 1] + w[p][t - 1]
            prob += v[t] + y[p][t] <= 1
            if t + 1 < len(days):
                # carrying p both into and out of day t means no other changeover on day t
                prob += w[p][t] + w[p][t + 1] <= 1 + v[t]
        # don't let the plan "win" by running stock down to zero at the end of the horizon
        prob += inv[p][days[-1]] >= i0[p]

    for t in days:
        prob += pulp.lpSum(w[p][t] for p in products) <= 1
        prob += (pulp.lpSum(x[p][t] * (1.0 / rate[p]) for p in products)
                 + settings.setup_hours * pulp.lpSum(y[p][t] for p in products) <= cap)

    prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit, gapRel=0.03))
    status = pulp.LpStatus[prob.status]

    rows = []
    for t in days:
        for p in products:
            made = x[p][t].value() or 0.0
            rows.append({
                "day": t,
                "product": p,
                "demand": d[p, t],
                "produced": round(made, 1),
                "setup": int(round(y[p][t].value() or 0)),
                "carried_in": int(round(w[p][t].value() or 0)),
                "carried_out": int(round(w[p][t + 1].value() or 0)) if t + 1 < len(days) else 0,
                "run_hours": round(made / rate[p], 2),
                "stock_end": round(inv[p][t].value() or 0.0, 1),
                "backorder_end": round(back[p][t].value() or 0.0, 1),
            })
    plan = pd.DataFrame(rows)
    return ScheduleResult(status=status, objective=objective, plan=plan,
                          kpis=compute_kpis(plan, settings))


def compute_kpis(plan: pd.DataFrame, settings: LineSettings) -> dict[str, float]:
    n_days = plan["day"].nunique()
    available = settings.hours_per_day * n_days
    run_hours = plan["run_hours"].sum()
    n_setups = int(plan["setup"].sum())
    avg_daily_demand = plan["demand"].sum() / n_days
    avg_stock = plan.groupby("day")["stock_end"].sum().mean()
    total_demand = plan["demand"].sum()
    # units that were late on at least one day (new backorders created)
    late_units = plan.groupby("product")["backorder_end"].apply(
        lambda s: s.diff().clip(lower=0).fillna(s.iloc[0]).sum()).sum()
    cost_setup = settings.setup_cost * n_setups
    cost_hold = settings.holding_cost * plan["stock_end"].sum()
    cost_back = settings.backorder_cost * plan["backorder_end"].sum()
    return {
        "utilization_pct": 100 * run_hours / available,
        "productive_pct": 100 * run_hours / (run_hours + n_setups * settings.setup_hours)
        if run_hours else 0.0,
        "changeovers": n_setups,
        "setup_hours": n_setups * settings.setup_hours,
        "avg_stock_units": avg_stock,
        "avg_stock_days": avg_stock / avg_daily_demand if avg_daily_demand else 0.0,
        "fill_rate_pct": 100 * (1 - late_units / total_demand) if total_demand else 100.0,
        "cost_setup": cost_setup,
        "cost_holding": cost_hold,
        "cost_backorder": cost_back,
        "cost_total": cost_setup + cost_hold + cost_back,
    }


def gantt_rows(plan: pd.DataFrame, settings: LineSettings) -> pd.DataFrame:
    """Turn the daily plan into start/finish blocks for a Gantt chart.

    Order within a day: the product carried in from yesterday runs first (no
    changeover), then new setups, with the product carried into tomorrow last.
    """
    blocks = []
    for t, day in plan.groupby("day"):
        clock = t * 24.0
        active = day[(day["setup"] == 1) | (day["carried_in"] == 1)].copy()
        active["order"] = (active["setup"] == 1).astype(int) + active["carried_out"] * 2
        active.loc[active["carried_in"] == 1, "order"] = -1
        for _, r in active.sort_values("order").iterrows():
            if r["setup"] == 1 and r["carried_in"] == 0:
                blocks.append({"product": "Changeover", "start": clock,
                               "finish": clock + settings.setup_hours,
                               "detail": f"to {r['product']}"})
                clock += settings.setup_hours
            if r["run_hours"] > 0:
                blocks.append({"product": r["product"], "start": clock,
                               "finish": clock + r["run_hours"],
                               "detail": f"{r['produced']:.0f} units"})
                clock += r["run_hours"]
    return pd.DataFrame(blocks)
