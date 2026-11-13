import pandas as pd

from scheduler import LineSettings, default_initial_stock, default_rates, gantt_rows, sample_demand, solve


def _settings(**kw):
    return LineSettings(rates=default_rates(), initial_stock=default_initial_stock(2), **kw)


def test_meets_demand_and_capacity():
    demand = sample_demand(days=7)
    s = _settings()
    res = solve(demand, s, "total_cost", time_limit=5)
    plan = res.plan
    # capacity respected every day
    for day, grp in plan.groupby("day"):
        used = grp["run_hours"].sum() + s.setup_hours * grp["setup"].sum()
        assert used <= s.hours_per_day + 1e-6
    # nothing produced without a setup or a carried-over setup
    assert ((plan["produced"] > 0) <= ((plan["setup"] + plan["carried_in"]) > 0)).all()
    # ends with at least the starting stock
    end = plan[plan["day"] == plan["day"].max()].set_index("product")["stock_end"]
    for p, start in s.initial_stock.items():
        assert end[p] >= start - 1e-6


def test_utilization_objective_uses_fewer_changeovers_but_more_stock():
    demand = sample_demand(days=10)
    s = _settings()
    cost = solve(demand, s, "total_cost", time_limit=5).kpis
    util = solve(demand, s, "utilization", time_limit=5).kpis
    assert util["changeovers"] <= cost["changeovers"]
    assert util["avg_stock_units"] >= cost["avg_stock_units"]
    assert util["cost_total"] >= cost["cost_total"] - 1e-6


def test_gantt_fits_in_each_day():
    demand = sample_demand(days=7)
    s = _settings()
    res = solve(demand, s, "total_cost", time_limit=5)
    g = gantt_rows(res.plan, s)
    assert isinstance(g, pd.DataFrame) and not g.empty
    assert ((g["finish"] - (g["start"] // 24) * 24) <= 24 + 1e-6).all()
