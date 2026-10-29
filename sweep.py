"""
Efficiency and output vs flue gas inlet temperature, for the end of each
cycle tab in app.py.

The curves are the pre-computed temperature sweeps from
Optimization_Final.ipynb (each point is a full cycle solve, too slow to
redo in the app), stored in sweep_data.json:
    t_flue_in_c : flue gas inlet temperature (°C) -- the notebook dict keys
    output_kw   : tuple element [0], net power (kW) or cooling (kW) for LiBr
    energy      : tuple element [3], energy efficiency (%) or COP for LiBr
    exergy_pct  : tuple element [4], exergy efficiency (%); the sCO2 values
                  are already multiplied by 100
Start temperatures: sCO2 230 °C, transcritical CO2 130 °C, steam 190 °C,
ORC and LiBr the full sweep.
"""
import json
import os

KW_PER_TON = 3.517   # 1 ton of refrigeration = 3.517 kW of cooling

RED, BLUE, GREEN, PURPLE = "#d62728", "#1f77b4", "#2ca02c", "#7b3fa0"
TITLES = {"super": "Supercritical CO₂ Brayton Cycle", "trans": "Transcritical CO₂ Rankine Cycle",
          "steam": "Steam Rankine Cycle", "orc": "Organic Rankine Cycle (ORC)",
          "libr": "LiBr Vapour Absorption Cycle"}

_DATA = None


def _load(name):
    """Reads a data file that must sit in the same folder as app.py."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
    if not os.path.exists(path):
        raise FileNotFoundError(f"{name} is missing. Upload it to the same folder as app.py in your "
                                f"GitHub repo, then reboot the app.")
    with open(path) as fh:
        return json.load(fh)


def data(key):
    global _DATA
    if _DATA is None:
        _DATA = _load("sweep_data.json")["cycles"]
    return _DATA[key]


def _layout(fig, title):
    fig.update_layout(title_text=title, height=500, hovermode="x unified",
                      margin=dict(l=10, r=10, t=50, b=10),
                      legend=dict(orientation="h", yanchor="top", y=-0.15, x=0.5, xanchor="center"))
    fig.update_xaxes(title_text="Flue gas inlet temperature (°C)")


def power_cycle_figure(key):
    """Energy and exergy efficiency (left axis, %) and net power (right axis, kW)."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    d = data(key)
    t = d["t_flue_in_c"]
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Scatter(x=t, y=d["energy"], name="Energy efficiency", line=dict(color=RED, width=2.5),
                             hovertemplate="%{y:.2f} %"), secondary_y=False)
    fig.add_trace(go.Scatter(x=t, y=d["exergy_pct"], name="Exergy efficiency", line=dict(color=BLUE, width=2.5),
                             hovertemplate="%{y:.2f} %"), secondary_y=False)
    fig.add_trace(go.Scatter(x=t, y=d["output_kw"], name="Net power", line=dict(color=GREEN, width=2.5, dash="dash"),
                             hovertemplate="%{y:,.1f} kW"), secondary_y=True)
    _layout(fig, TITLES[key])
    fig.update_yaxes(title_text="Efficiency (%)", secondary_y=False)
    fig.update_yaxes(title_text="Net power (kW)", secondary_y=True, showgrid=False, tickmode="auto",
                     title_font_color=GREEN, tickfont_color=GREEN)
    return fig


def libr_figure():
    """COP (left axis), exergy efficiency (right axis, %) and cooling (second right axis, tons)."""
    import plotly.graph_objects as go
    d = data("libr")
    t = d["t_flue_in_c"]
    tons = [kw / KW_PER_TON for kw in d["output_kw"]]
    cop_max = max(1.0, 1.2 * max(d["energy"]))
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=t, y=d["energy"], name="COP", line=dict(color=RED, width=2.5),
                             hovertemplate="%{y:.3f}", yaxis="y"))
    fig.add_trace(go.Scatter(x=t, y=d["exergy_pct"], name="Exergy efficiency", line=dict(color=BLUE, width=2.5),
                             hovertemplate="%{y:.2f} %", yaxis="y2"))
    fig.add_trace(go.Scatter(x=t, y=tons, name="Cooling", line=dict(color=GREEN, width=2.5, dash="dash"),
                             hovertemplate="%{y:,.1f} tons", yaxis="y3"))
    _layout(fig, TITLES["libr"])
    fig.update_layout(
        xaxis=dict(domain=[0, 0.86]),
        # COP is essentially constant across the sweep, so start its axis at 0
        yaxis=dict(title=dict(text="COP", font=dict(color=RED)), tickfont=dict(color=RED), range=[0, cop_max]),
        yaxis2=dict(title=dict(text="Exergy efficiency (%)", font=dict(color=BLUE)), tickfont=dict(color=BLUE),
                    overlaying="y", side="right", anchor="x", showgrid=False, tickmode="auto"),
        yaxis3=dict(title=dict(text="Cooling (tons of refrigeration)", font=dict(color=GREEN)),
                    tickfont=dict(color=GREEN), overlaying="y", side="right", anchor="free",
                    position=0.97, showgrid=False, tickmode="auto"),
    )
    return fig


def figure(key):
    return libr_figure() if key == "libr" else power_cycle_figure(key)


# ---------------------------------------------------------------------------
# Cascade: flue gas passed through the cycles one after another
# ---------------------------------------------------------------------------
# cascade_data.json holds the results of Final_just_power.ipynb: for every
# flue gas inlet temperature, the best order of the five cycles (all
# permutations tried, flue gas passed from one cycle to the next until it
# drops below 100 °C), with cycles giving zero or negative power ignored and
# LiBr cooling counted as power = cooling / 5.4.

_CASCADE = None


def cascade_data():
    global _CASCADE
    if _CASCADE is None:
        _CASCADE = _load("cascade_data.json")
    return _CASCADE


SHORT = {"Supercritical CO₂": "sCO₂", "Steam Rankine": "Steam", "Transcritical CO₂": "tCO₂",
         "ORC": "ORC", "LiBr (cooling ÷ 5.4)": "LiBr"}
BAND_COLORS = ["#8dd3c7", "#fdb462", "#bebada", "#80b1d3", "#fb8072", "#b3de69", "#fccde5", "#ffffb3"]


def cascade_ranges():
    """Consecutive inlet-temperature ranges using the same cycle combination
    (in order). Returns [{"combo", "t_from", "t_to", "power_from", "power_to",
    "cost_from", "cost_to"}]."""
    runs = []
    for p in cascade_data()["points"]:
        combo = " → ".join(SHORT.get(s["cycle"], s["cycle"]) for s in p["stages"]) or "none"
        if runs and runs[-1]["combo"] == combo:
            runs[-1].update(t_to=p["t_flue_in_c"], power_to=p["power_kw"], cost_to=p["cost_inr"])
        else:
            runs.append({"combo": combo, "t_from": p["t_flue_in_c"], "t_to": p["t_flue_in_c"],
                         "power_from": p["power_kw"], "power_to": p["power_kw"],
                         "cost_from": p["cost_inr"], "cost_to": p["cost_inr"]})
    return runs


def cascade_figure():
    """Total power (left axis, kW) and final flue gas exit temperature
    (right axis, °C) vs flue gas inlet temperature. The background is shaded
    by the best combination of cycles in each inlet-temperature range, and
    the hover lists that combination with each cycle's power."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    pts = cascade_data()["points"]
    t = [p["t_flue_in_c"] for p in pts]
    order = ["<br>".join(f"{i + 1}. {s['cycle']}: {s['power_kw']:,.1f} kW, ₹ {s['cost_inr']:,.0f} "
                         f"({s['t_in_c']} → {s['t_out_c']} °C)" for i, s in enumerate(p["stages"]))
             or "no cycle produces power" for p in pts]
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    # Shaded bands: one colour per combination, in order of first appearance
    runs = cascade_ranges()
    colors = {}
    for r in runs:
        colors.setdefault(r["combo"], BAND_COLORS[len(colors) % len(BAND_COLORS)])
    for r in runs:
        fig.add_shape(type="rect", xref="x", yref="paper", x0=r["t_from"] - 0.5, x1=r["t_to"] + 0.5,
                      y0=0, y1=1, fillcolor=colors[r["combo"]], opacity=0.35, line_width=0, layer="below")
        if r["t_to"] - r["t_from"] >= 60:          # label the wide bands; the legend covers all
            fig.add_annotation(x=(r["t_from"] + r["t_to"]) / 2, y=1.0, yref="paper", yanchor="bottom",
                               text=r["combo"].replace(" → ", " →<br>"), showarrow=False,
                               font=dict(size=11, color="#333"))
    for combo, color in colors.items():            # legend entries for the bands
        fig.add_trace(go.Scatter(x=[None], y=[None], mode="markers", name=combo, legendgroup="bands",
                                 legendgrouptitle_text="Best combination", hoverinfo="skip",
                                 marker=dict(symbol="square", size=14, color=color)), secondary_y=False)

    fig.add_trace(go.Scatter(x=t, y=[p["power_kw"] for p in pts], name="Total power", customdata=order,
                             line=dict(color=GREEN, width=2.5),
                             hovertemplate="<b>%{y:,.1f} kW</b><br>%{customdata}<extra>Total power</extra>"),
                  secondary_y=False)
    fig.add_trace(go.Scatter(x=t, y=[p["t_exit_c"] for p in pts], name="Flue gas exit temperature",
                             line=dict(color=RED, width=2.5, dash="dash"),
                             hovertemplate="%{y:.0f} °C<extra>Flue gas exit</extra>"), secondary_y=True)
    # Total cost of the cycles in the best combination, on a third axis (₹ crore)
    fig.add_trace(go.Scatter(x=t, y=[p["cost_inr"] / 1e7 for p in pts], name="Total cost",
                             customdata=[p["cost_inr"] for p in pts], yaxis="y3",
                             line=dict(color=PURPLE, width=2.5, dash="dot"),
                             hovertemplate="₹ %{customdata:,.0f}<extra>Total cost</extra>"))
    _layout(fig, "SOFC Waste Heat Recovery – best combination of cycles")
    fig.update_layout(hovermode="closest", height=600, margin=dict(l=10, r=10, t=110, b=10),
                      plot_bgcolor="white",
                      legend=dict(orientation="h", yanchor="top", y=-0.15, x=0.5, xanchor="center"))
    fig.update_xaxes(range=[t[0] - 0.5, t[-1] + 0.5], showgrid=False)
    fig.update_yaxes(title_text="Total power (kW)", secondary_y=False, title_font_color=GREEN,
                     tickfont_color=GREEN, gridcolor="rgba(0,0,0,0.08)")
    fig.update_yaxes(title_text="Flue gas exit temperature (°C)", secondary_y=True, showgrid=False,
                     tickmode="auto", title_font_color=RED, tickfont_color=RED)
    fig.update_layout(
        xaxis=dict(domain=[0, 0.86]),
        yaxis3=dict(title=dict(text="Total cost (₹ crore)", font=dict(color=PURPLE)),
                    tickfont=dict(color=PURPLE), overlaying="y", side="right", anchor="free",
                    position=0.97, showgrid=False, tickmode="auto", rangemode="tozero"))
    return fig


# ---------------------------------------------------------------------------
# Lowest-LCOE combination of cycles
# ---------------------------------------------------------------------------
# lcoe_data.json holds the results of LCOE.ipynb: for every flue gas inlet
# temperature, every ordered subset of the five cycles is tried (flue gas
# passed from one cycle to the next until it drops below 100 °C, cycles with
# no power ignored), and the combination with the lowest levelised cost of
# electricity is kept. LiBr cooling counts as power = cooling / 5.4.

_LCOE = None


def lcoe_data():
    global _LCOE
    if _LCOE is None:
        _LCOE = _load("lcoe_data.json")
    return _LCOE


def lcoe_ranges():
    """Consecutive inlet-temperature ranges with the same lowest-LCOE cycle group."""
    runs = []
    for p in lcoe_data()["points"]:
        if runs and runs[-1]["group"] == p["group"]:
            r = runs[-1]
            r["t_to"] = p["t_flue_in_c"]
            r["lcoe_min"], r["lcoe_max"] = min(r["lcoe_min"], p["lcoe"]), max(r["lcoe_max"], p["lcoe"])
            r["power_to"] = p["power_kw"]
        else:
            runs.append({"group": p["group"], "t_from": p["t_flue_in_c"], "t_to": p["t_flue_in_c"],
                         "lcoe_min": p["lcoe"], "lcoe_max": p["lcoe"],
                         "power_from": p["power_kw"], "power_to": p["power_kw"]})
    return runs


def _lcoe_hover(p):
    lines = []
    for i, s in enumerate(p["stages"]):
        out = (f"{s['cooling_kw']:,.1f} kW cooling ({s['cooling_kw'] / KW_PER_TON:,.1f} TR)"
               if s["cycle"].startswith("LiBr") else f"{s['power_kw']:,.1f} kW power")
        lines.append(f"{i + 1}. {s['cycle']}: {out}, capex ₹ {s['capex_inr']:,.0f} "
                     f"({s['t_in_c']} → {s['t_out_c']:.0f} °C)")
    annual = p["annual_capital"] + p["fixed_om"] + p["variable_om"]
    lines.append(f"Annual cost ₹ {annual:,.0f}/yr = capital ₹ {p['annual_capital']:,.0f} "
                 f"+ fixed O&M ₹ {p['fixed_om']:,.0f} + variable O&M ₹ {p['variable_om']:,.0f}")
    lines.append(f"Generation {p['generation_kwh']:,.0f} kWh/yr")
    return "<br>".join(lines)


def lcoe_figure():
    """LCOE (left axis, ₹/kWh), equivalent power (right axis, kW) and flue gas
    exit temperature (second right axis, °C) vs flue gas inlet temperature,
    with the background shaded by the lowest-LCOE cycle group."""
    import plotly.graph_objects as go
    pts = lcoe_data()["points"]
    t = [p["t_flue_in_c"] for p in pts]
    fig = go.Figure()

    runs = lcoe_ranges()
    colors = {}
    for r in runs:
        colors.setdefault(r["group"], BAND_COLORS[len(colors) % len(BAND_COLORS)])
    for r in runs:
        fig.add_shape(type="rect", xref="x", yref="paper", x0=r["t_from"] - 0.5, x1=r["t_to"] + 0.5,
                      y0=0, y1=1, fillcolor=colors[r["group"]], opacity=0.35, line_width=0, layer="below")
        if r["t_to"] - r["t_from"] >= 25:          # label the wide bands; the legend covers all
            fig.add_annotation(x=(r["t_from"] + r["t_to"]) / 2, y=1.0, yref="paper", yanchor="bottom",
                               text=r["group"], showarrow=False, font=dict(size=11, color="#333"))
    for group, color in colors.items():
        fig.add_trace(go.Scatter(x=[None], y=[None], mode="markers", name=group, legendgroup="bands",
                                 legendgrouptitle_text="Lowest-LCOE cycle group", hoverinfo="skip",
                                 marker=dict(symbol="square", size=14, color=color)))

    fig.add_trace(go.Scatter(x=t, y=[p["lcoe"] for p in pts], name="LCOE", yaxis="y",
                             customdata=[[p["group"], _lcoe_hover(p)] for p in pts],
                             line=dict(color="#111", width=2.5),
                             hovertemplate="<b>%{customdata[0]}: %{y:.3f} ₹/kWh</b><br>%{customdata[1]}"
                                           "<extra>LCOE</extra>"))
    fig.add_trace(go.Scatter(x=t, y=[p["power_kw"] for p in pts], name="Equivalent power", yaxis="y2",
                             line=dict(color=BLUE, width=2.5, dash="dash"),
                             hovertemplate="%{y:,.1f} kW<extra>Equivalent power</extra>"))
    fig.add_trace(go.Scatter(x=t, y=[p["t_exit_c"] for p in pts], name="Flue gas exit temperature",
                             yaxis="y3", line=dict(color=GREEN, width=2.5, dash="dot"),
                             hovertemplate="%{y:.0f} °C<extra>Flue gas exit</extra>"))

    _layout(fig, "Lowest-LCOE combination of cycles")
    fig.update_layout(
        hovermode="closest", height=600, margin=dict(l=10, r=10, t=80, b=10), plot_bgcolor="white",
        legend=dict(orientation="h", yanchor="top", y=-0.15, x=0.5, xanchor="center"),
        xaxis=dict(domain=[0, 0.86], range=[t[0] - 0.5, t[-1] + 0.5], showgrid=False),
        yaxis=dict(title=dict(text="LCOE (₹/kWh)"), gridcolor="rgba(0,0,0,0.08)", rangemode="tozero"),
        yaxis2=dict(title=dict(text="Equivalent power (kW)", font=dict(color=BLUE)), tickfont=dict(color=BLUE),
                    overlaying="y", side="right", anchor="x", showgrid=False, tickmode="auto",
                    rangemode="tozero"),
        yaxis3=dict(title=dict(text="Flue gas exit temperature (°C)", font=dict(color=GREEN)),
                    tickfont=dict(color=GREEN), overlaying="y", side="right", anchor="free", position=0.97,
                    showgrid=False, tickmode="auto"),
    )
    return fig
