"""Production Scheduler: the utilization trap.

Run with:  streamlit run app.py
"""

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from scheduler import (LineSettings, default_initial_stock, default_rates,
                       gantt_rows, sample_demand, solve)

NAVY, TEAL, RED, GREY = "#0F2A44", "#0E9F8A", "#D64545", "#5B6B7A"
PRODUCT_COLORS = {"Product A": NAVY, "Product B": "#3C7DB0", "Product C": "#7FB8D9",
                  "Product D": "#B9A26B", "Changeover": RED}
MODES = {
    "utilization": "Plant KPI: fewest changeovers",
    "total_cost": "Supply chain KPI: lowest total cost",
}

st.set_page_config(page_title="Production Scheduler", layout="wide")

st.title("Production Scheduler: the utilization trap")
st.caption("One line, four products, two weeks. A mixed integer program (Python + PuLP) decides "
           "what to make each day. Compare what happens when you optimise for the plant's KPI "
           "versus the total cost of changeovers, inventory and late orders.")

# ---------------- inputs ----------------
with st.sidebar:
    st.header("Line settings")
    hours = st.slider("Available hours per day", 16.0, 24.0, 22.0, 0.5)
    setup_h = st.slider("Changeover time (hours)", 0.5, 4.0, 2.0, 0.5)
    st.header("Costs")
    setup_cost = st.slider("Cost per changeover", 0, 1000, 150, 25)
    hold_cost = st.slider("Holding cost per unit per day", 0.00, 0.50, 0.10, 0.01)
    back_cost = st.slider("Late order cost per unit per day", 0.0, 20.0, 5.0, 0.5)
    st.header("Demand")
    days = st.slider("Planning horizon (days)", 7, 21, 14)
    scale = st.slider("Demand level (x normal)", 0.6, 1.3, 1.0, 0.05)
    cover = st.slider("Starting stock (days of demand)", 0.0, 4.0, 2.0, 0.5)
    seed = st.number_input("Random seed", 1, 999, 7)
    upload = st.file_uploader("Or upload demand CSV (rows = days, columns = products)", type="csv")

if upload is not None:
    demand = pd.read_csv(upload)
    demand = demand.select_dtypes("number").reset_index(drop=True)
else:
    demand = sample_demand(days=days, seed=int(seed), scale=scale)

rates = default_rates()
for p in demand.columns:
    rates.setdefault(p, 150.0)
start_stock = default_initial_stock(cover)
start_stock = {p: start_stock.get(p, float(demand[p].mean()) * cover) for p in demand.columns}

settings = LineSettings(hours_per_day=hours, setup_hours=setup_h, setup_cost=setup_cost,
                        holding_cost=hold_cost, backorder_cost=back_cost,
                        rates=rates, initial_stock=start_stock)

load = sum(demand[p].mean() / rates[p] for p in demand.columns)
st.info(f"The line needs about **{load:.1f} of {hours:.0f} hours a day** just to make what customers buy. "
        f"Every changeover burns **{setup_h:g} hours** of what's left.")


@st.cache_data(show_spinner=False)
def run(demand_csv: str, s: dict, objective: str):
    d = pd.read_csv(pd.io.common.StringIO(demand_csv))
    return solve(d, LineSettings(**s), objective)


key_settings = dict(hours_per_day=hours, setup_hours=setup_h, setup_cost=setup_cost,
                    holding_cost=hold_cost, backorder_cost=back_cost,
                    rates=rates, initial_stock=start_stock)
demand_csv = demand.to_csv(index=False)

with st.spinner("Optimising both schedules (a few seconds each)..."):
    results = {obj: run(demand_csv, key_settings, obj) for obj in MODES}

# ---------------- KPI comparison ----------------
st.subheader("Same line. Same demand. Two KPIs.")
cols = st.columns(2)
ku, kc = results["utilization"].kpis, results["total_cost"].kpis
for col, obj in zip(cols, MODES):
    k = results[obj].kpis
    other = kc if obj == "utilization" else ku
    with col:
        st.markdown(f"#### {MODES[obj]}")
        a, b, c = st.columns(3)
        a.metric("Changeovers", int(k["changeovers"]),
                 delta=int(k["changeovers"] - other["changeovers"]), delta_color="inverse")
        b.metric("Time producing", f"{k['productive_pct']:.0f}%",
                 help="Run hours as a share of run + changeover hours")
        c.metric("Average stock", f"{k['avg_stock_days']:.1f} days",
                 delta=f"{k['avg_stock_days'] - other['avg_stock_days']:+.1f}", delta_color="inverse")
        a, b, c = st.columns(3)
        a.metric("Total cost", f"{k['cost_total']:,.0f}",
                 delta=f"{k['cost_total'] - other['cost_total']:+,.0f}", delta_color="inverse")
        b.metric("Fill rate", f"{k['fill_rate_pct']:.1f}%")
        c.metric("Changeover hours", f"{k['setup_hours']:.0f}")

# cost breakdown chart
cost_df = pd.DataFrame([
    {"KPI": MODES[o], "Cost type": t, "Cost": results[o].kpis[f"cost_{key}"]}
    for o in MODES for t, key in [("Changeovers", "setup"), ("Holding stock", "holding"),
                                  ("Late orders", "backorder")]
])
fig_cost = px.bar(cost_df, x="Cost", y="KPI", color="Cost type", orientation="h",
                  color_discrete_map={"Changeovers": RED, "Holding stock": NAVY, "Late orders": "#B9A26B"},
                  title="Where the money goes")
fig_cost.update_layout(height=260, margin=dict(l=10, r=10, t=50, b=10), yaxis_title=None,
                       legend_title=None, legend=dict(orientation="h", y=-0.3))
st.plotly_chart(fig_cost, width="stretch", config={"displayModeBar": False})

# ---------------- schedules ----------------
st.subheader("The schedules")
st.caption("Each bar is one day on the line. Red slivers are changeovers.")


def gantt_fig(res):
    g = gantt_rows(res.plan, settings)
    fig = go.Figure()
    for prod, grp in g.groupby("product"):
        fig.add_trace(go.Bar(
            x=grp["finish"] - grp["start"], base=grp["start"], y=["Line"] * len(grp),
            orientation="h", name=prod, marker_color=PRODUCT_COLORS.get(prod, GREY),
            hovertext=grp["detail"], hoverinfo="text+name"))
    fig.update_layout(barmode="overlay", height=190, margin=dict(l=10, r=10, t=40, b=30),
                      title=f"{MODES[res.objective]}: {int(res.kpis['changeovers'])} changeovers",
                      xaxis=dict(tickvals=[24 * i for i in range(len(demand) + 1)],
                                 ticktext=[f"Day {i}" if i % 2 == 0 else "" for i in range(len(demand) + 1)]),
                      yaxis=dict(showticklabels=False), showlegend=(res.objective == "total_cost"),
                      legend=dict(orientation="h", y=-0.35))
    return fig


for obj in MODES:
    st.plotly_chart(gantt_fig(results[obj]), width="stretch", key=f"gantt_{obj}", config={"displayModeBar": False})

st.subheader("Stock on hand")
ymax = max(results[o].plan.groupby("day")["stock_end"].sum().max() for o in MODES) * 1.1
cols = st.columns(2)
for col, obj in zip(cols, MODES):
    res = results[obj]
    stock = res.plan.pivot(index="day", columns="product", values="stock_end")
    fig_s = px.area(stock, labels={"value": "Units in stock", "day": "Day"},
                    color_discrete_map=PRODUCT_COLORS,
                    title=f"{MODES[obj]}: {res.kpis['avg_stock_days']:.1f} days on average")
    fig_s.update_layout(height=320, margin=dict(l=10, r=10, t=50, b=10), legend_title=None,
                        yaxis=dict(range=[0, ymax]), showlegend=False)
    col.plotly_chart(fig_s, width="stretch", key=f"stock_{obj}", config={"displayModeBar": False})

with st.expander("Daily plan tables"):
    for obj in MODES:
        st.markdown(f"**{MODES[obj]}**")
        st.dataframe(results[obj].plan, width="stretch", hide_index=True)
        st.download_button("Download as CSV", results[obj].plan.to_csv(index=False),
                           file_name=f"plan_{obj}.csv", key=f"dl_{obj}")

with st.expander("How the model works"):
    st.markdown(r"""
**Decision variables** for each product *p* and day *t*: units made $x_{pt}$, a changeover $y_{pt}\in\{0,1\}$,
setup carried over from yesterday $w_{pt}\in\{0,1\}$, stock $I_{pt}$ and late units $B_{pt}$.

**Constraints**
- Flow: $I_{p,t-1} - B_{p,t-1} + x_{pt} - d_{pt} = I_{pt} - B_{pt}$
- Capacity: $\sum_p x_{pt}/r_p + s\sum_p y_{pt} \le H$
- Make only if set up: $x_{pt} \le M_{pt}(y_{pt} + w_{pt})$
- Setup carryover: the line can start a day already set up for at most one product, and only if it ran that product the day before.

**Objectives**
- *Plant KPI*: meet demand with as few changeovers as possible (stock is almost invisible to it).
- *Supply chain KPI*: minimise $S\sum y + h\sum I + b\sum B$.

Solved with the open-source CBC solver via PuLP, with an 8 second limit and a 3% optimality gap.
All data is synthetic.
""")
