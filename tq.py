"""
Temperature-heat (T-Q) diagram of the flue gas heater for the steam and ORC
cycles.

profile() recomputes the 101 nodes that uo.py's hx_pinch_steam() /
hx_pinch_orc() step through in get_pinch() to find the pinch point, using
the same equations, at the converged heater duty:

    node i, q_frac = i / 100, cumulative duty Q_i = q_frac * Q  (from the
    cold-fluid inlet end)
    cold side:  h_c = h_c,in + Q_i / m_c,   p_c = p_c,in - q_frac * dp_c
    hot side:   h_h = h_h,in - (Q - Q_i) / m_h,   p_h = p_h,out + q_frac * dp_h

Counter-flow: at the cold-fluid inlet end (Q = 0) the flue gas leaves, and
at the cold-fluid outlet end (Q = Q_total) the flue gas enters.

The cycle files store the converged heater inputs as `cycle.heater_args`.
"""
import CoolProp.CoolProp as CP

from uo import find_t_from_h

N_STEPS = 100


def profile(hot_fluid, p_hot_in, t_hot_in, m_hot, hot_drop, cold_fluid, p_cold_in, h_cold_in,
            m_cold, cold_drop, q_kw, pinch, t_low_offset=1):
    """Returns plain lists (kW, °C) for plotting, plus the pinch found."""
    q = float(q_kw) * 1000.0
    if q <= 0:
        return None
    p_hot_out = p_hot_in - hot_drop
    h_hot_in = CP.PropsSI('H', "T|gas", t_hot_in, "P", p_hot_in, hot_fluid)
    # lower bracket for the flue gas temperature solve -- same as uo.py
    t_flue_cond = CP.PropsSI('T', "Q|gas", 1, "P", p_hot_out, hot_fluid)

    q_nodes, t_hot, t_cold = [], [], []
    for i in range(N_STEPS + 1):
        q_frac = i / N_STEPS
        h_c = h_cold_in + (q_frac * q) / m_cold
        p_c = p_cold_in - q_frac * cold_drop
        h_h = h_hot_in - ((1.0 - q_frac) * q) / m_hot
        p_h = p_hot_out + q_frac * hot_drop
        t_cold.append(CP.PropsSI('T', "H", h_c, "P", p_c, cold_fluid) - 273.15)
        t_hot.append(find_t_from_h(h_h, p_h, t_flue_cond - t_low_offset, 702 + 273.15, hot_fluid) - 273.15)
        q_nodes.append(q_frac * q / 1000.0)

    dts = [h - c for h, c in zip(t_hot, t_cold)]
    k = min(range(len(dts)), key=dts.__getitem__)
    return dict(q=q_nodes, t_hot=t_hot, t_cold=t_cold, pinch_q=q_nodes[k], pinch_dt=dts[k],
                pinch_t_hot=t_hot[k], pinch_t_cold=t_cold[k], pinch_target=float(pinch),
                q_total=float(q_kw))


def figure(tq, title, hot_name, cold_name):
    import plotly.graph_objects as go
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=tq["q"], y=tq["t_hot"], mode="lines+markers", name=hot_name,
                             line=dict(color="#e53935", width=2.5), marker=dict(size=4),
                             hovertemplate="Q = %{x:,.1f} kW<br>T = %{y:.1f} °C<extra>" + hot_name + "</extra>"))
    fig.add_trace(go.Scatter(x=tq["q"], y=tq["t_cold"], mode="lines+markers", name=cold_name,
                             line=dict(color="#1e88e5", width=2.5), marker=dict(size=4),
                             hovertemplate="Q = %{x:,.1f} kW<br>T = %{y:.1f} °C<extra>" + cold_name + "</extra>"))
    # pinch point
    fig.add_trace(go.Scatter(x=[tq["pinch_q"], tq["pinch_q"]], y=[tq["pinch_t_cold"], tq["pinch_t_hot"]],
                             mode="lines+markers", name=f"Pinch ΔT = {tq['pinch_dt']:.1f} K",
                             line=dict(color="#212121", width=2, dash="dot"), marker=dict(size=8, symbol="diamond"),
                             hovertemplate="Pinch<br>Q = %{x:,.1f} kW<br>T = %{y:.1f} °C<extra></extra>"))
    # flow-direction arrows (counter-flow)
    n = len(tq["q"])
    for ys, color, rev in ((tq["t_hot"], "#e53935", True), (tq["t_cold"], "#1e88e5", False)):
        a, b = (int(n * 0.62), int(n * 0.52)) if rev else (int(n * 0.38), int(n * 0.48))
        fig.add_annotation(x=tq["q"][b], y=ys[b], ax=tq["q"][a], ay=ys[a], xref="x", yref="y",
                           axref="x", ayref="y", showarrow=True, arrowhead=3, arrowsize=1.4,
                           arrowwidth=2, arrowcolor=color, text="")
    fig.update_layout(title_text=title, height=480, margin=dict(l=10, r=10, t=50, b=10),
                      xaxis_title="Heat transferred, Q (kW)", yaxis_title="Temperature (°C)",
                      legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0), hovermode="closest")
    return fig
