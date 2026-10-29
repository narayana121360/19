import json
import os
import subprocess
import sys

import numpy as np
import streamlit as st

import sankey
import sweep
import tq
from flue import FUELS, composition

st.set_page_config(page_title="SOFC Flue gas Recovery Analysis", page_icon="🔥", layout="wide")

st.title("SOFC Flue gas Recovery Analysis")
st.header("SOFC Flue gas Composition and flow rate Calculation")
st.caption("Flue gas composition and mass flow of the SOFC exhaust (after the afterburner).")

BAR = 1e5
K = 273.15

# ---------------- Inputs ----------------
with st.sidebar:
    st.header("Inputs")
    with st.form("inputs"):
        fuel = st.selectbox(
            "Fuel",
            options=list(FUELS.keys()),
            format_func=lambda f: f.upper() if f in ("lpg", "co") else f.capitalize(),
        )
        power = st.number_input("Energy Content (kW)", min_value=0.001, value=8000.0, step=100.0)
        t_c = st.number_input("Flue Gas Inlet Temperature (°C)", min_value=30.0, value=650.0, step=10.0)
        p_bar = st.number_input("Flue Gas Inlet Pressure (bar)", min_value=0.1, value=2.0, step=0.1)
        u_ratio = st.number_input("SOFC Utilization Ratio (%)", min_value=1.0, max_value=100.0, value=60.0, step=1.0)
        sc_ratio = st.number_input("Steam Carbon Ratio", min_value=0.0, value=1.2, step=0.1)
        excess_air = st.number_input("Excess Air (%)", min_value=0.0, value=300.0, step=10.0)
        submitted = st.form_submit_button("Calculate", type="primary", width="stretch")

# ---------------- Calculation ----------------
try:
    c = composition(fuel, power, t_c + K, p_bar * BAR, u_ratio, sc_ratio, excess_air)
    c.verbose = False
    mole_fractions, mass_flow = c.calc()
except ValueError as e:
    st.error(str(e))
    st.stop()
except Exception as e:  # CoolProp failures (e.g. condensing conditions)
    st.error(f"Property calculation failed for these conditions: {e}")
    st.stop()

# Same mixture string the notebook builds and passes to every cycle
flue_string = (f"HEOS::Nitrogen[{c.mf_nitrogen}]&Oxygen[{c.mf_oxygen}]"
               f"&Water[{c.mf_water}]&CO2[{c.mf_co2}]")

# ---------------- Outputs ----------------
labels = ["N₂", "O₂", "H₂O", "CO₂"]

st.subheader(f"Results for {fuel.capitalize()}")
m1, m2 = st.columns(2)
m1.metric("Flue Gas Mass Flow Rate", f"{mass_flow:.4f} kg/s")
m2.metric("Flue Gas Mass Flow Rate", f"{mass_flow * 3600:,.1f} kg/h")

st.markdown("#### Flue Gas Composition")
cols = st.columns(4)
for col, name, x in zip(cols, labels, mole_fractions):
    col.metric(name, f"{x * 100:.2f} %")


# ======================================================================
# Waste heat recovery cycles
# ======================================================================
# Each cycle is solved only when its Run button is pressed, and results are
# cached on the inputs, because the IDAES-based cycles (sCO2, tCO2) take
# minutes to solve.

def _sankey(key, cyc):
    """Exergy Sankey data; a diagram problem never fails the cycle run."""
    try:
        return sankey.build_flows(key, cyc.exergy)
    except Exception as err:
        return {"error": str(err)}


def _tq(cyc):
    """Heater T-Q profile; a diagram problem never fails the cycle run."""
    try:
        return tq.profile(**cyc.heater_args)
    except Exception as err:
        return {"error": str(err)}


def _rows(rows):
    """Cost-table rows with plain floats (cached results must be picklable/JSON-like)."""
    return [{k: (float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v)
             for k, v in row.items()} for row in (rows or [])]


def _breakdown(cost, names):
    out = {}
    for key, label in names.items():
        d = cost.get(key)
        out[label] = None if not d else d.get("cost")
    return out


RUNNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "co2_runner.py")
RESULT_TAG = "__CO2_RUNNER_RESULT__"


def _call_runner(task, args=None):
    """Runs co2_runner.py in a fresh Python process (see that file for why)
    and returns its JSON result."""
    proc = subprocess.run([sys.executable, RUNNER, task], input=json.dumps(args or {}),
                          capture_output=True, text=True, cwd=os.path.dirname(RUNNER))
    for line in reversed(proc.stdout.splitlines()):
        if line.startswith(RESULT_TAG):
            return json.loads(line[len(RESULT_TAG):])
    raise RuntimeError("The cycle process stopped unexpectedly:\n" + (proc.stderr or proc.stdout)[-1500:])


def _solve_in_runner(task, args):
    out = _call_runner(task, args)
    if "error" in out:
        raise RuntimeError(out["error"])  # raised, not returned, so failures are not cached
    return out


@st.cache_data(show_spinner=False)
def run_super(flue, approach, p1, bypass, htr_eff, ltr_eff, drop_hot, drop_cold, bc, cooler,
              turb_eff, mc_eff, bc_eff):
    return _solve_in_runner("super", dict(flue=flue, approach=approach, p1=p1, bypass=bypass,
                                          htr_eff=htr_eff, ltr_eff=ltr_eff, drop_hot=drop_hot,
                                          drop_cold=drop_cold, bc=bc, cooler=cooler,
                                          turb_eff=turb_eff, mc_eff=mc_eff, bc_eff=bc_eff))


@st.cache_data(show_spinner=False)
def run_steam(t_in, p_in, m_flue, fs, turb_eff, pump_eff, losses, pinch, t_cond, subcool):
    import steam
    cyc = steam.steam(t_in, p_in, m_flue, fs, turb_eff, pump_eff, list(losses),
                      pinch=pinch, condensing_temp=t_cond, subcooling=subcool)
    en, exg, net, f_out, cost = cyc.calc()
    return dict(energy=en, exergy=exg, output=net, heat_in=cyc.Heater,
                t_out=f_out[0], total=cost["total"],
                cw_kgs=float(cyc.cooling_tower_cost["flow_kgs"]), cw_m3h=float(cyc.cooling_tower_cost["flow_m3hr"]),
                extras=[], cost_table=_rows(cyc.cost_table), fluid_costs=_rows(cyc.fluid_costs),
                tq=_tq(cyc),
                costs=_breakdown(cost, {"turbine": "Turbine", "pump": "Pump", "heater": "Heater (boiler)",
                                        "condenser": "Condenser", "cooling_tower": "Cooling tower"}),
                sankey=_sankey("steam", cyc))


@st.cache_data(show_spinner=False)
def run_libr(t_in, p_in, m_flue, fs, t_eva, t_cond, shx_eff, pump_eff, losses, circ, weak):
    import libr
    cyc = libr.libr(t_in, p_in, m_flue, fs, t_eva, t_cond, shx_eff, pump_eff, tuple(losses), circ,
                    weak_concentration=weak)
    cop, exg, cooling, f_out, cost = cyc.calc()
    costs = _breakdown(cost, {"desorber": "Desorber (generator)", "absorber": "Absorber",
                              "condenser": "Condenser", "evaporator": "Evaporator",
                              "solution_hx": "Solution HX", "pump": "Pump",
                              "cooling_tower": "Cooling tower", "working_fluid": "LiBr charge"})
    return dict(energy=float(cop), exergy=float(exg), output=float(cooling), heat_in=float(cyc.q_heating),
                t_out=float(f_out[0]), total=float(cost["total"]),
                costs={k: (None if v is None else float(v)) for k, v in costs.items()},
                cw_kgs=None, cw_m3h=float(cyc.flowrate_water),
                extras=[["Weak Solution Concentration", f"{100 * weak:.2f} % LiBr"],
                        ["Strong Solution Concentration", f"{100 * cyc.strong_concentration:.2f} % LiBr"]],
                cost_table=_rows(cyc.cost_table), fluid_costs=_rows(cyc.fluid_costs),
                sankey=_sankey("libr", cyc))


@st.cache_data(show_spinner=False)
def run_orc(t_in, p_in, m_flue, fs, turb_eff, pump_eff, losses, pinch, t_cond, subcool):
    import orc
    cyc = orc.orc(t_in, p_in, m_flue, fs, turb_eff, pump_eff, tuple(losses),
                  pinch=pinch, condensing_temp=t_cond, subcooling=subcool)
    en, exg, net, f_out, cost = cyc.calc()
    return dict(energy=en, exergy=exg, output=net, heat_in=cyc.hx_heater,
                t_out=f_out[0], total=cost["total"],
                cw_kgs=float(cyc.cooling_tower_cost["flow_kgs"]), cw_m3h=float(cyc.cooling_tower_cost["flow_m3hr"]),
                extras=[], cost_table=_rows(cyc.cost_table), fluid_costs=_rows(cyc.fluid_costs),
                tq=_tq(cyc),
                costs=_breakdown(cost, {"turbine": "Turbine", "pump": "Pump", "heater": "Evaporator",
                                        "condenser": "Condenser", "cooling_tower": "Cooling tower",
                                        "working_fluid": "Isobutane charge"}),
                sankey=_sankey("orc", cyc))


@st.cache_data(show_spinner=False)
def run_trans(t_in, p_in, m_flue, fs, turb_eff, pump_eff, heater_eff, losses):
    return _solve_in_runner("trans", dict(t_in=t_in, p_in=p_in, m_flue=m_flue, fs=fs, turb_eff=turb_eff,
                                          pump_eff=pump_eff, heater_eff=heater_eff, losses=losses))


def run(key, fn, *args):
    with st.spinner("Solving cycle… (the CO₂ cycles can take several minutes)"):
        try:
            st.session_state[key] = fn(*args)
        except Exception as err:
            st.session_state[key] = {"error": str(err)}


SANKEY_TITLES = {"res_super": "Supercritical CO₂ cycle",
                 "res_steam": "Steam Rankine cycle",
                 "res_libr": "LiBr absorption chiller",
                 "res_orc": "Organic Rankine cycle",
                 "res_trans": "Transcritical CO₂ cycle"}


def show_results(key, cooling=False):
    r = st.session_state.get(key)
    if r is None:
        return
    if "error" in r:
        st.error(f"The cycle could not be solved for these inputs: {r['error']}")
        return
    a, b, cc = st.columns(3)
    if cooling:
        a.metric("COP", f"{r['energy']:.3f}")
        b.metric("Exergy Efficiency", f"{r['exergy']:.2f} %")
        cc.metric("Cooling Capacity", f"{r['output']:,.1f} kW")
    else:
        a.metric("Energy Efficiency", f"{r['energy']:.2f} %")
        b.metric("Exergy Efficiency", f"{r['exergy']:.2f} %")
        cc.metric("Net Power Output", f"{r['output']:,.1f} kW")
    d, e, f = st.columns(3)
    d.metric("Heat Recovered from Flue Gas", f"{r['heat_in']:,.1f} kW")
    e.metric("Flue Gas Exit Temperature", f"{r['t_out'] - K:.1f} °C")
    total = r["total"]
    f.metric("Total Plant Cost", "N/A" if total is None else f"₹ {total:,.0f}")
    # Cooling water (condenser / cooling tower) flow, plus cycle-specific outputs
    tiles = []
    if r.get("cw_m3h") is not None:
        cw = f"{r['cw_m3h']:,.1f} m³/h"
        if r.get("cw_kgs") is not None:
            cw = f"{r['cw_kgs']:,.2f} kg/s ({r['cw_m3h']:,.1f} m³/h)"
        tiles.append(["Cooling Water Flow Rate", cw])
    tiles += r.get("extras", [])
    if tiles:
        for col, (label, value) in zip(st.columns(3), tiles[:3]):
            col.metric(label, value)
        for col, (label, value) in zip(st.columns(3), tiles[3:]):
            col.metric(label, value)
    sk = r.get("sankey")
    if sk and "error" not in sk:
        st.markdown("#### Exergy Sankey diagrams")
        st.caption(f"Exergy efficiency = {sk['formula']} = **{r['exergy']:.2f} %** "
                   "(green ÷ orange in the overall exergy balance). "
                   "Exergy rates in kW. Orange: exergy input · Green: useful output · "
                   "Red: exergy destruction · Blue: exergy leaving with the cooling water or to the "
                   "bottoming cycle · Grey: exergy circulating inside the cycle.")
        for i, d in enumerate(sk["diagrams"]):
            st.plotly_chart(sankey.figure(d["flows"], f"{SANKEY_TITLES[key]} – {d['name']}"),
                            width="stretch", key=f"sankey_{key}_{i}")
    elif sk:
        st.warning(f"The exergy Sankey diagrams could not be drawn: {sk['error']}")
    show_tq(key, r.get("tq"))
    show_costs(r)


TQ_NAMES = {"res_steam": ("Steam Rankine cycle – heater (boiler)", "Water / steam"),
            "res_orc": ("Organic Rankine cycle – heater (evaporator)", "Isobutane")}


def show_tq(key, data):
    if not data or key not in TQ_NAMES:
        return
    st.markdown("#### Heater T-Q diagram")
    if "error" in data:
        st.warning(f"The T-Q diagram could not be drawn: {data['error']}")
        return
    title, cold_name = TQ_NAMES[key]
    st.plotly_chart(tq.figure(data, title, "Flue gas", cold_name), width="stretch", key=f"tq_{key}")
    st.caption(f"Counter-flow heater, {len(data['q'])} nodes – the same points used to find the pinch. "
               f"Q is the heat transferred, counted from the {cold_name.lower()} inlet end, where the "
               f"flue gas leaves. Heater duty: {data['q_total']:,.1f} kW. Pinch: {data['pinch_dt']:.1f} K "
               f"(target {data['pinch_target']:.1f} K) at Q = {data['pinch_q']:,.1f} kW.")


COST_FORMATS = {"Size": "{:,.2f}", "Base capacity": "{:,.2f}", "Base cost (2024)": "₹ {:,.0f}",
                "Exponent": "{:.2f}", "Material factor": "{:.2f}", "Temperature factor": "{:.2f}",
                "Pressure factor": "{:.2f}", "Piping factor": "{:.2f}", "U (W/m²·K)": "{:,.0f}",
                "F-factor": "{:.2f}", "LMTD (K)": "{:.2f}", "Cost (₹, Mar 2026)": "₹ {:,.0f}"}
FLUID_FORMATS = {"Charge (kg)": "{:,.1f}", "Price (₹/kg)": "₹ {:,.2f}", "Cost (₹)": "₹ {:,.0f}"}


def show_costs(r):
    import pandas as pd
    st.markdown("#### Cost breakdown")
    rows = r.get("cost_table")
    if not rows:
        return
    df = pd.DataFrame(rows)
    if not df["Note"].astype(bool).any():
        df = df.drop(columns="Note")
    st.markdown("**Equipment**")
    st.dataframe(df.style.format(COST_FORMATS, na_rep="–"), hide_index=True, width="stretch")
    st.caption("Cost = Base cost × (Size ÷ Base capacity)^Exponent × Material × Temperature × Pressure "
               "× (1 + Piping), escalated from the 2024 basis to March 2026. Heat-exchanger area = "
               "Duty ÷ (U × F-factor × LMTD), with the counter-flow LMTD from the terminal temperatures. "
               "U, F-factor and LMTD apply to heat exchangers only.")
    fluids = r.get("fluid_costs")
    if fluids:
        st.markdown("**Working fluid**")
        st.dataframe(pd.DataFrame(fluids).style.format(FLUID_FORMATS, na_rep="–"),
                     hide_index=True, width="stretch")
    equip = sum(row["Cost (₹, Mar 2026)"] or 0 for row in rows)
    fluid_total = sum(row["Cost (₹)"] or 0 for row in (fluids or []))
    parts = [f"**Equipment:** ₹ {equip:,.0f}"]
    if fluids:
        parts.append(f"**Working fluid:** ₹ {fluid_total:,.0f}")
    parts.append(f"**Total plant cost:** ₹ {r['total']:,.0f}")
    st.markdown(" · ".join(parts))


@st.cache_resource(show_spinner=False)
def ensure_idaes():
    """Makes sure the IDAES add-ons needed by the CO2 cycles are installed,
    downloading them automatically if not. Runs once per server (shared by
    every user of the app), so on Streamlit Cloud the first CO2 run after a
    reboot does the setup and later runs start straight away. A failed
    attempt is not cached, so the next run tries again."""
    status = _call_runner("status")
    if status["ok"]:
        return status
    result = _call_runner("install")
    status = _call_runner("status")
    if not status["ok"]:
        raise RuntimeError(status["message"] + "\n\nAutomatic install log:\n" + result.get("log", ""))
    return status


def idaes_ready():
    """Called when a CO2 cycle's Run button is pressed. Returns True when the
    cycle can run; otherwise shows what went wrong."""
    with st.spinner("Preparing the CO₂ property models… the first run after the app "
                    "starts downloads them, which can take a few minutes"):
        try:
            ensure_idaes()
            return True
        except RuntimeError as err:
            st.error("The IDAES add-ons needed for this cycle could not be set up automatically. "
                     "Press Run again to retry.")
            with st.expander("Details"):
                st.code(str(err)[-3000:])
            return False


SWEEP_NOTES = {
    "super": "From 230 °C flue gas inlet temperature.",
    "trans": "From 130 °C flue gas inlet temperature.",
    "steam": "From 190 °C flue gas inlet temperature.",
    "orc": "Full temperature range of the sweep.",
    "libr": "COP on the left axis; exergy efficiency and cooling (1 ton = 3.517 kW) on the right.",
}


def show_sweep(key):
    """Pre-computed efficiency / output curves vs flue gas inlet temperature,
    shown only after the cycle has been run successfully in this session."""
    r = st.session_state.get(f"res_{key}")
    if not r or "error" in r:
        return
    st.markdown("#### Performance vs flue gas inlet temperature")
    try:
        st.plotly_chart(sweep.figure(key), width="stretch", key=f"sweep_{key}")
    except Exception as err:
        st.warning(f"The performance curves could not be drawn: {err}")
        return
    st.caption("Pre-computed temperature sweep (Optimization_Final.ipynb); these curves do not change "
               "with the inputs above. " + SWEEP_NOTES[key])


def drop(label, default, key):
    """Pressure-drop input in bar, returned in Pa."""
    return st.number_input(f"{label} (bar)", min_value=0.0, value=default, step=0.1, format="%.2f", key=key) * BAR


st.divider()
st.header("Waste Heat Recovery Cycles")
st.caption("Each cycle uses the flue gas composition and mass flow rate calculated above. "
           "Set the flue gas inlet conditions for each cycle and press Run.")

tabs = st.tabs(["Supercritical CO₂", "Steam Rankine", "LiBr Absorption", "Organic Rankine (ORC)",
                "Transcritical CO₂"])

# ---------------- Supercritical CO2 ----------------
with tabs[0]:
    st.caption("Recompression sCO₂ Brayton cycle. The flue gas enters at the SOFC flue gas "
               "temperature and pressure set in the sidebar.")
    with st.form("super"):
        a, b, cc = st.columns(3)
        approach = a.number_input("Flue Gas HX Approach (K)", min_value=1.0, value=20.0, step=1.0)
        p1 = b.number_input("Turbine Inlet Pressure (bar)", min_value=80.0, value=239.0, step=1.0)
        bypass = cc.number_input("Bypass Fraction", min_value=0.0, max_value=0.9, value=0.348, step=0.01, format="%.3f")
        a, b = st.columns(2)
        htr = a.number_input("HTR Effectiveness", min_value=0.1, max_value=0.999, value=0.973982, step=0.01, format="%.4f")
        ltr = b.number_input("LTR Effectiveness", min_value=0.1, max_value=0.999, value=0.936902, step=0.01, format="%.4f")
        a, b, cc = st.columns(3)
        s_te = a.number_input("Turbine Isentropic Efficiency", min_value=0.1, max_value=1.0, value=0.85, step=0.01, key="s_te")
        s_mc = b.number_input("Main Compressor Efficiency", min_value=0.1, max_value=1.0, value=0.82, step=0.01, key="s_mc")
        s_bc = cc.number_input("Bypass Compressor Efficiency", min_value=0.1, max_value=1.0, value=0.78, step=0.01, key="s_bc")
        st.markdown("**Bottoming cycle and cooling water**")
        with st.container():
            a, b, cc = st.columns(3)
            t_bc = a.number_input("Bottoming Cycle CO₂ Inlet Temperature (°C)", value=40.0)
            p_bc = b.number_input("Bottoming Cycle CO₂ Inlet Pressure (bar)", value=120.0)
            m_bc = cc.number_input("Bottoming Cycle CO₂ Flow (kg/s)", min_value=0.01, value=0.33)
            a, b = st.columns(2)
            t_cw = a.number_input("Cooling Water Inlet Temperature (°C)", value=25.0, key="s_tcw")
            p_cw = b.number_input("Cooling Water Inlet Pressure (bar)", value=2.5, key="s_pcw")
        st.markdown("**Pressure drops**")
        with st.container():
            a, b = st.columns(2)
            with a:
                st.markdown("**Hot side**")
                hot = [drop("HTR", 2.0, "sh1"), drop("LTR", 2.0, "sh2"), drop("Bottoming-cycle HX", 1.0, "sh3"),
                       drop("Cooler", 1.0, "sh4"), 0.0, drop("Flue gas HX, flue side", 0.2, "sh6")]
            with b:
                st.markdown("**Cold side**")
                cold = [drop("HTR", 6.0, "sc1"), drop("LTR", 5.0, "sc2"), drop("Bottoming-cycle HX", 4.0, "sc3"),
                        drop("Cooler", 1.0, "sc4"), 0.0, drop("Flue gas HX, CO₂ side", 0.2, "sc6")]
        go = st.form_submit_button("Run Supercritical CO₂ Cycle", type="primary")
    if go and idaes_ready():
        run("res_super", run_super, (t_c + K, p_bar * BAR, mass_flow, flue_string), approach, p1 * BAR,
            bypass, htr, ltr, tuple(hot), tuple(cold), (t_bc + K, p_bc * BAR, m_bc, "CO2"),
            (t_cw + K, p_cw * BAR, "IF97::Water"), s_te, s_mc, s_bc)
    show_results("res_super")
    show_sweep("super")

# ---------------- Steam Rankine ----------------
with tabs[1]:
    st.caption("Steam Rankine cycle. The water flow rate is optimised for maximum net power.")
    with st.form("steam"):
        a, b = st.columns(2)
        t_in = a.number_input("Flue Gas Inlet Temperature (°C)", min_value=60.0, value=400.0, step=10.0, key="st_t")
        p_in = b.number_input("Flue Gas Inlet Pressure (bar)", min_value=0.5, value=1.8, step=0.1, key="st_p")
        a, b = st.columns(2)
        te = a.number_input("Turbine Isentropic Efficiency", min_value=0.1, max_value=1.0, value=0.75, step=0.01, key="st_te")
        pe = b.number_input("Pump Efficiency", min_value=0.1, max_value=1.0, value=0.75, step=0.01, key="st_pe")
        a, b, cc = st.columns(3)
        st_pinch = a.number_input("Pinch Temperature (K)", min_value=1.0, value=10.0, step=1.0, key="st_pinch")
        st_tc = b.number_input("Condensing Temperature (°C)", min_value=32.0, value=40.0, step=1.0, key="st_tc",
                               help="Cooling water enters at 25 °C and leaves at 31 °C, so keep this above 31 °C.")
        st_sc = cc.number_input("Subcooling (K)", min_value=0.0, value=5.0, step=1.0, key="st_sc")
        st.markdown("**Pressure drops**")
        with st.container():
            a, b = st.columns(2)
            with a:
                l1 = drop("Condenser, steam side", 0.0, "st_l1")
                l3 = drop("Boiler, flue gas side", 0.2, "st_l3")
            with b:
                l2 = drop("Condenser, cooling water side", 1.0, "st_l2")
                l4 = drop("Boiler, water side", 2.0, "st_l4")
        go = st.form_submit_button("Run Steam Rankine Cycle", type="primary")
    if go:
        run("res_steam", run_steam, t_in + K, p_in * BAR, mass_flow, flue_string, te, pe, (l1, l2, l3, l4),
            st_pinch, st_tc + K, st_sc)
    show_results("res_steam")
    show_sweep("steam")

# ---------------- LiBr absorption ----------------
with tabs[2]:
    st.caption("Single-effect LiBr–water vapour absorption chiller driven by the flue gas.")
    with st.form("libr"):
        a, b = st.columns(2)
        t_in = a.number_input("Flue Gas Inlet Temperature (°C)", min_value=60.0, value=140.88, step=5.0, key="li_t")
        p_in = b.number_input("Flue Gas Inlet Pressure (bar)", min_value=0.5, value=1.6, step=0.1, key="li_p")
        a, b = st.columns(2)
        t_eva = a.number_input("Evaporating Temperature (°C)", value=1.0, step=1.0)
        t_cond = b.number_input("Condensing Temperature (°C)", value=40.0, step=1.0)
        a, b = st.columns(2)
        weak = a.number_input("Weak Solution Concentration (LiBr mass fraction)", min_value=0.40, max_value=0.70,
                              value=0.5648, step=0.005, format="%.4f")
        circ = b.number_input("Circulation Ratio", min_value=1.1, value=10.94, step=0.1)
        a, b = st.columns(2)
        shx = a.number_input("Solution HX Effectiveness", min_value=0.1, max_value=1.0, value=0.65, step=0.01)
        pe = b.number_input("Pump Efficiency", min_value=0.1, max_value=1.0, value=0.75, step=0.01, key="li_pe")
        st.markdown("**Pressure drops**")
        with st.container():
            a, b = st.columns(2)
            with a:
                l1 = drop("Solution HX, hot side", 0.0, "li_l1")
                l3 = drop("Desorber, flue gas side", 0.2, "li_l3")
            with b:
                l2 = drop("Solution HX, cold side", 0.0, "li_l2")
                l4 = drop("Desorber, solution side", 0.0, "li_l4")
        go = st.form_submit_button("Run LiBr Absorption Cycle", type="primary")
    if go:
        run("res_libr", run_libr, t_in + K, p_in * BAR, mass_flow, flue_string, t_eva + K, t_cond + K,
            shx, pe, (l1, l2, l3, l4), circ, weak)
    show_results("res_libr", cooling=True)
    show_sweep("libr")

# ---------------- ORC ----------------
with tabs[3]:
    st.caption("Isobutane organic Rankine cycle. The isobutane flow rate is optimised for maximum net power.")
    with st.form("orc"):
        a, b = st.columns(2)
        t_in = a.number_input("Flue Gas Inlet Temperature (°C)", min_value=60.0, value=200.0, step=10.0, key="or_t")
        p_in = b.number_input("Flue Gas Inlet Pressure (bar)", min_value=0.5, value=1.6, step=0.1, key="or_p")
        a, b = st.columns(2)
        te = a.number_input("Turbine Isentropic Efficiency", min_value=0.1, max_value=1.0, value=0.70, step=0.01, key="or_te")
        pe = b.number_input("Pump Efficiency", min_value=0.1, max_value=1.0, value=0.75, step=0.01, key="or_pe")
        a, b, cc = st.columns(3)
        or_pinch = a.number_input("Pinch Temperature (K)", min_value=1.0, value=10.0, step=1.0, key="or_pinch")
        or_tc = b.number_input("Condensing Temperature (°C)", min_value=31.0, value=40.0, step=1.0, key="or_tc",
                               help="Cooling water enters at 25 °C and leaves at 30 °C, so keep this above 30 °C.")
        or_sc = cc.number_input("Subcooling (K)", min_value=0.0, value=5.0, step=1.0, key="or_sc")
        st.markdown("**Pressure drops**")
        with st.container():
            a, b = st.columns(2)
            with a:
                l1 = drop("Condenser, isobutane side", 0.0, "or_l1")
                l3 = drop("Evaporator, flue gas side", 0.0, "or_l3")
            with b:
                l2 = drop("Condenser, cooling water side", 0.0, "or_l2")
                l4 = drop("Evaporator, isobutane side", 0.0, "or_l4")
        go = st.form_submit_button("Run Organic Rankine Cycle", type="primary")
    if go:
        run("res_orc", run_orc, t_in + K, p_in * BAR, mass_flow, flue_string, te, pe, (l1, l2, l3, l4),
            or_pinch, or_tc + K, or_sc)
    show_results("res_orc")
    show_sweep("orc")

# ---------------- Transcritical CO2 ----------------
with tabs[4]:
    st.caption("Transcritical CO₂ Rankine cycle.")
    with st.form("trans"):
        a, b = st.columns(2)
        t_in = a.number_input("Flue Gas Inlet Temperature (°C)", min_value=60.0, value=700.0, step=10.0, key="tr_t")
        p_in = b.number_input("Flue Gas Inlet Pressure (bar)", min_value=0.5, value=1.6, step=0.1, key="tr_p")
        a, b, cc = st.columns(3)
        te = a.number_input("Turbine Isentropic Efficiency", min_value=0.1, max_value=1.0, value=0.70, step=0.01, key="tr_te")
        pe = b.number_input("Pump Efficiency", min_value=0.1, max_value=1.0, value=0.75, step=0.01, key="tr_pe")
        he = cc.number_input("Heater Effectiveness", min_value=0.1, max_value=1.0, value=0.90, step=0.01)
        st.markdown("**Pressure drops**")
        with st.container():
            a, b = st.columns(2)
            with a:
                l1 = drop("Condenser, CO₂ side", 0.0, "tr_l1")
                l3 = drop("Heater, flue gas side", 0.0, "tr_l3")
            with b:
                l2 = drop("Condenser, cooling water side", 0.0, "tr_l2")
                l4 = drop("Heater, CO₂ side", 0.0, "tr_l4")
        go = st.form_submit_button("Run Transcritical CO₂ Cycle", type="primary")
    if go and idaes_ready():
        run("res_trans", run_trans, t_in + K, p_in * BAR, mass_flow, flue_string, te, pe, he, (l1, l2, l3, l4))
    show_results("res_trans")
    show_sweep("trans")


# ---------------- Cascade of all cycles ----------------
st.divider()
st.header("Combined Waste Heat Recovery")
st.caption("The flue gas is passed through the cycles one after another. Every order of the five cycles is "
           "tried, and the order giving the most total power is kept. The flue gas moves to the next cycle "
           "until its temperature drops below 100 °C. Cycles giving zero or negative power are ignored, and "
           "LiBr cooling (in kW) is counted as power = cooling ÷ 5.4. The total cost is the sum of the costs of the "
           "cycles in the best combination (the order is chosen for maximum power, not minimum cost). "
           "Hover over the power curve to see the best order of cycles at each inlet temperature, with "
           "each cycle's power and cost.")
try:
    st.plotly_chart(sweep.cascade_figure(), width="stretch", key="cascade")
    import pandas as pd
    st.markdown("**Best combination of cycles by flue gas inlet temperature**")
    st.dataframe(pd.DataFrame([{
        "Flue gas inlet temperature (°C)": f"{r['t_from']}–{r['t_to']}",
        "Cycles, in order of flue gas flow": r["combo"],
        "Total power (kW)": f"{r['power_from']:,.1f} – {r['power_to']:,.1f}",
        "Total cost (₹)": f"{r['cost_from']:,.0f} – {r['cost_to']:,.0f}",
    } for r in sweep.cascade_ranges()]), hide_index=True, width="stretch")
except Exception as err:
    st.warning(f"The combined waste heat recovery plot could not be drawn: {err}")
st.caption("Pre-computed (Final_power_and_cost.ipynb, using the cycle results from Optimization_Final.ipynb); "
           "this plot does not change with the inputs above.")


# ---------------- Lowest-LCOE combination of cycles ----------------
st.divider()
st.header("Lowest-LCOE Combination of Cycles")
try:
    _econ = sweep.lcoe_data()["economics"]
    st.caption(
        "The flue gas is passed through the cycles one after another, as above, but here every ordered "
        "subset of the five cycles (1 to 5 cycles) is tried and the one with the lowest levelised cost of "
        "electricity is kept, so a cycle whose cost outweighs the power it adds is left out. "
        f"LCOE = (capex × CRF + fixed O&M + variable O&M) ÷ annual generation, with a "
        f"{100 * _econ['discount_rate']:.0f} % discount rate, {_econ['lifetime_years']}-year life "
        f"(CRF {_econ['crf']:.4f}) and {_econ['operating_hours']:,.0f} operating hours per year. "
        f"LiBr cooling is counted as power = cooling ÷ {_econ['cop_libr']}. Hover over the LCOE curve "
        "for the cycle-by-cycle breakdown at each inlet temperature.")
    st.plotly_chart(sweep.lcoe_figure(), width="stretch", key="lcoe")
    import pandas as pd
    st.markdown("**Lowest-LCOE cycle group by flue gas inlet temperature**")
    st.dataframe(pd.DataFrame([{
        "Flue gas inlet temperature (°C)": f"{r['t_from']}–{r['t_to']}",
        "Cycles, in order of flue gas flow": r["group"],
        "LCOE (₹/kWh)": f"{r['lcoe_min']:.3f} – {r['lcoe_max']:.3f}",
        "Equivalent power (kW)": f"{r['power_from']:,.1f} – {r['power_to']:,.1f}",
    } for r in sweep.lcoe_ranges()]), hide_index=True, width="stretch")
    st.caption("Pre-computed (LCOE.ipynb, using the cycle results from Optimization_Final.ipynb); "
               "this plot does not change with the inputs above.")
except Exception as err:
    st.warning(f"The LCOE plot could not be drawn: {err}")
