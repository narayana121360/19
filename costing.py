"""
Cost-table rows for app.py.

Each cycle's calc() collects, for every costed component, the cost dict it
already computes plus the cost factors (and U / F values for heat
exchangers) it used, by calling item() / fluid() below, and stores the list
as `cycle.cost_table` / `cycle.fluid_costs`. Rows are plain, JSON-safe
dicts so the IDAES cycles can send them back from co2_runner.py.

Base cost (2024 basis), base capacity and exponent come from uo.py's
COST_TABLE -- the same numbers equipment_cost() uses:
    Cost = Base_Cost * (Capacity / Base_Capacity) ** Exponent
           * material * temperature * pressure * (1 + piping)
then escalated to March 2026 in each cycle file.
"""
from uo import COST_TABLE

# Where each kind of cost dict keeps its sizing capacity
_CAPACITY_KEY = {
    "turbine": "capacity_m3hr",
    "compressor": "capacity_m3hr",
    "pump": "capacity_kw",
    "heat_exchanger": "area_m2",
    "cooling_tower": "flow_m3hr",
}
_UNITS = {"m3/hr": "m³/h", "m2": "m²", "kW": "kW"}


def _num(x):
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def item(name, kind, cost_dict, factors=None, f=None, u=None, capacity=None, cost=None, note=None):
    """One equipment row.

    kind: 'turbine', 'compressor', 'pump', 'heat_exchanger', 'cooling_tower'
          (priced with uo.py's COST_TABLE), or 'idaes' for CO2 turbomachinery
          priced with the IDAES sCO2 costing module (no base cost/exponent).
    cost_dict: the component's cost dict from the cycle (gives capacity,
          U for heat exchangers, and the final cost) -- may be None.
    """
    cost_dict = cost_dict or {}
    factors = factors or {}
    table = COST_TABLE.get(kind, {})
    if capacity is None:
        capacity = cost_dict.get(_CAPACITY_KEY.get(kind, ""))
    if u is None and kind == "heat_exchanger":
        u = cost_dict.get("u_assumed")
    if cost is None:
        cost = cost_dict.get("cost") if isinstance(cost_dict, dict) else None
    unit = "kW (shaft power)" if kind == "idaes" else _UNITS.get(table.get("unit"), table.get("unit"))
    return {
        "Component": name,
        "Size": _num(capacity),
        "Size unit": unit,
        "Base capacity": _num(table.get("base_capacity")),
        "Base cost (2024)": _num(table.get("base_cost")),
        "Exponent": _num(table.get("exponent")),
        "Material factor": _num(factors.get("material_factor")) if kind != "idaes" else None,
        "Temperature factor": _num(factors.get("temperature_factor")) if kind != "idaes" else None,
        "Pressure factor": _num(factors.get("pressure_factor")) if kind != "idaes" else None,
        "Piping factor": _num(factors.get("piping_factor")) if kind != "idaes" else None,
        "U (W/m²·K)": _num(u) if kind == "heat_exchanger" else None,
        "F-factor": _num(f) if kind == "heat_exchanger" else None,
        "LMTD (K)": _num(cost_dict.get("lmtd_k")) if kind == "heat_exchanger" else None,
        "Cost (₹, Mar 2026)": _num(cost),
        "Note": note or ("IDAES sCO₂ costing module" if kind == "idaes" else ""),
    }


def fluid(name, mass_kg, price_per_kg, cost, basis):
    """One working-fluid row."""
    return {
        "Working fluid": name,
        "Charge (kg)": _num(mass_kg),
        "Price (₹/kg)": _num(price_per_kg),
        "Cost (₹)": _num(cost),
        "Charge basis": basis,
    }
