"""Run explicit JSON inputs through the two-layer solver (standard library only).

Usage: python3 -m src.geoscience.simulate examples/solver-smoke.json --output outputs/smoke.json
The shipped example is synthetic numerical verification, not an Incheon/WCC run.
"""

from __future__ import annotations

import argparse
import json
from bisect import bisect_right
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .model import Budget, Forcing, Layer, Parameters, State, advance, energy
from .physics import darcy_flux, finite


def table_function(rows: list[list[float]]):
    """Linear interpolation inside a provided material curve; never extrapolate."""
    if len(rows) < 2 or any(len(row) != 2 for row in rows):
        raise ValueError("a curve needs at least two [water_kg_m2, value] rows")
    x, y = zip(*rows)
    for value in (*x, *y):
        finite("curve coordinate", value)
    if any(b <= a for a, b in zip(x, x[1:])):
        raise ValueError("curve water coordinates must be strictly increasing")

    def interpolate(water):
        finite("water coordinate", water)
        if not x[0] <= water <= x[-1]:
            raise ValueError(f"water {water} outside supplied curve [{x[0]}, {x[-1]}]")
        i = min(bisect_right(x, water) - 1, len(x) - 2)
        return y[i] + (y[i + 1] - y[i]) * (water - x[i]) / (x[i + 1] - x[i])

    return interpolate


def components(job: dict):
    """Validate material laws without advancing the model."""
    spec = job["parameters"]
    layers = {}
    for name in ("surface", "base"):
        layer = dict(spec[name])
        if isinstance(layer["conductivity"], list):
            layer["conductivity"] = table_function(layer["conductivity"])
        layers[name] = Layer(**layer)
    params = Parameters(**{**spec, **layers})
    hydraulic = job["exchange"]
    if hydraulic["mode"] == "none":
        exchange = lambda state: 0.0
    elif hydraulic["mode"] == "darcy":
        hs = table_function(hydraulic["surface_head_curve"])
        hb = table_function(hydraulic["base_head_curve"])
        ks = table_function(hydraulic["surface_conductivity_curve"])
        kb = table_function(hydraulic["base_conductivity_curve"])

        def exchange(state):
            return darcy_flux(ks(state.surface_water), kb(state.base_water),
                              hs(state.surface_water), hb(state.base_water),
                              params.surface.depth_m, params.base.depth_m,
                              hydraulic["water_density_kg_m3"])
    elif hydraulic["mode"] == "bounded_bucket":
        down = finite("downward maximum", hydraulic["downward_max_mm_h"], 0) / 3600
        up = finite("upward maximum", hydraulic["upward_max_mm_h"], 0) / 3600

        def exchange(state):
            # Saturation-gradient phenomenology, not Darcy or measured K.
            delta = (state.surface_water / params.surface.max_water_kg_m2
                     - state.base_water / params.base.max_water_kg_m2)
            return down * max(delta, 0) - up * max(-delta, 0)
    else:
        raise ValueError("exchange mode must be none, darcy or bounded_bucket")
    evaporation = job.get("evaporation")
    if evaporation is None:
        curve = table_function(job["surface_resistance_curve"])
        resistance_for = lambda forcing: curve
    else:
        if evaporation["mode"] != "beta" or evaporation["shape"] not in (
            "linear_saturation", "constant_when_wet"
        ):
            raise ValueError("unsupported evaporation mode/shape")
        beta_max = finite("beta_max", evaporation["beta_max"], 0)
        if beta_max > 1:
            raise ValueError("beta_max must not exceed one")

        def resistance_for(forcing):
            def resistance(water):
                fraction = water / params.surface.max_water_kg_m2
                if not 0 <= fraction <= 1:
                    raise ValueError("evaporation water outside capacity")
                beta = beta_max * (fraction if evaporation["shape"] == "linear_saturation"
                                   else float(water > 0))
                return (forcing.aerodynamic_resistance_s_m * (1 / beta - 1)
                        if beta > 0 else float("inf"))
            return resistance
    return params, exchange, resistance_for


def run(job: dict, *, authorize_research: bool = False) -> dict:
    """Research inputs require explicit approval; ordinary synthetic tests do not."""
    if job.get("research_run") and not authorize_research:
        raise PermissionError("Final research simulation requires additional user approval")
    params, exchange, resistance_for = components(job)
    state = initial = State(**job["initial_state"])
    total, elapsed, rows = Budget(), 0.0, []
    if not job["intervals"]:
        raise ValueError("at least one forcing interval is required")
    for interval in job["intervals"]:
        duration = interval["duration_s"]
        finite("interval duration", duration, 0)
        if duration == 0:
            raise ValueError("forcing intervals must have positive duration")
        forcing = Forcing(**interval["forcing"])
        state, budget = advance(state, forcing, params, duration,
                                surface_resistance=resistance_for(forcing), exchange=exchange,
                                max_step_s=job["max_step_s"])
        elapsed += duration
        total += budget
        rows.append({"elapsed_s": elapsed, "state": asdict(state), "budget": asdict(budget)})
    expected_mass = (total.rain_kg_m2 + total.irrigation_kg_m2 - total.evaporated_kg_m2
                     - total.drained_kg_m2 - total.runoff_kg_m2)
    expected_heat = (total.absorbed_shortwave_j_m2 + total.net_longwave_j_m2
                     - total.sensible_out_j_m2 - total.latent_out_j_m2 - total.ground_out_j_m2
                     + total.water_enthalpy_net_j_m2)
    return {
        "label": job["label"], "evidence_note": job["evidence_note"],
        "input": job, "interval_results": rows, "total_budget": asdict(total),
        "whole_run_water_residual_kg_m2": state.surface_water + state.base_water
        - initial.surface_water - initial.base_water - expected_mass,
        "whole_run_energy_residual_j_m2": energy(state, params) - energy(initial, params) - expected_heat,
        "warning": "Numerical conservation is not experimental validation of WCC.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--weather", type=Path, help="prepared weather JSON; job must have no intervals")
    parser.add_argument("--start", help="KST start, YYYY-MM-DD HH:MM")
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--approve-final-comparison", action="store_true",
                        help="use only after the user explicitly authorizes research simulations")
    args = parser.parse_args()
    # Refuse replacement of an existing result or input; choose a new filename.
    if args.output.exists():
        parser.error(f"output already exists: {args.output}")
    with args.job.open(encoding="utf-8") as stream:
        job = json.load(stream)
    if bool(args.weather) != bool(args.start):
        parser.error("--weather and --start must be used together")
    if args.weather:
        from .weather import TIME_FORMAT, attach_weather
        weather = json.loads(args.weather.read_text(encoding="utf-8"))
        job = attach_weather(job, weather, datetime.strptime(args.start, TIME_FORMAT), args.hours)
    result = run(job, authorize_research=args.approve_final_comparison)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
    print(json.dumps({key: result[key] for key in (
        "label", "whole_run_water_residual_kg_m2", "whole_run_energy_residual_j_m2", "warning"
    )}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
