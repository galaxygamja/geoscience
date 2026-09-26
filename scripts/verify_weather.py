"""Reproducible integration proof, not a WCC prediction or a data replacement."""

from __future__ import annotations

import argparse
import copy
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.geoscience.preprocess import read_file, transform
from src.geoscience.simulate import run
from src.geoscience.weather import attach_weather, day_catalog, prepare


def verify(annual: Path) -> dict:
    raw, source = read_file(annual)
    manifest = json.loads(Path("data/source_manifest.json").read_text())
    if source["sha256"] != manifest["files"][0]["sha256"]:
        raise ValueError("annual input differs from the reviewed source manifest")
    daily = json.loads(Path("data/kma_daily_precipitation_2025.json").read_text())
    rows = prepare(transform(raw), wind_factor=1, deep_boundary="observed_30cm", daily=daily["days"])
    catalog = day_catalog(rows)
    candidates = [row for row in catalog if row["weather_ready"] and row["start_kst"][5:7] in ("07", "08")]
    if not candidates:
        raise ValueError("no complete summer weather window for integration proof")
    selected = candidates[0]
    bundle = {"hourly": rows, "source": source, "station": "112", "timezone": "Asia/Seoul",
              "daily_source": daily, "assumptions": {"wind_factor": 1, "deep_boundary": "observed_30cm"},
              "warning": "Real weather, but synthetic material used ONLY for software proof."}
    job = json.loads(Path("examples/solver-smoke.json").read_text())
    job.pop("intervals")
    job["label"] = "real-weather adapter software integration check; NOT WCC"
    joined = attach_weather(job, bundle, datetime.fromisoformat(selected["start_kst"]))
    proof = []
    states = []
    for step in (20, 10, 5):
        case = copy.deepcopy(joined)
        case["max_step_s"] = step
        result = run(case)
        mass = result["whole_run_water_residual_kg_m2"]
        heat = result["whole_run_energy_residual_j_m2"]
        if abs(mass) > 1e-8 or abs(heat) > 1e-5:
            raise AssertionError("weather-linked kernel conservation failed")
        states.append(result["interval_results"][-1]["state"]["surface_c"])
        proof.append({"max_step_s": step, "water_residual_kg_m2": mass, "energy_residual_j_m2": heat})
    differences = [abs(states[i + 1] - states[i]) for i in range(2)]
    if differences[1] > differences[0] + 1e-9:
        raise AssertionError("time-step refinement did not improve final temperature agreement")
    active = [r for r in rows if r["end_kst"][5:7] in ("01", "02", "07", "08")]
    months = {}
    for month in ("01", "02", "07", "08"):
        days = [d for d in catalog if d["start_kst"][5:7] == month]
        months[month] = {"candidate_days": len(days), "weather_ready_days": sum(d["weather_ready"] for d in days)}
    return {"source_sha256": source["sha256"], "source_hours": len(rows), "target_months": months,
            "ready_hours_all_year": sum(r["forcing"] is not None for r in rows),
            "target_month_precipitation_methods": dict(Counter(r["precipitation"]["reason"] for r in active)),
            "target_month_blocking_reasons": dict(Counter(reason for r in active for reason in r["blocking_reasons"])),
            "selected_weather_window": selected, "integration_proof": proof,
            "successive_final_temperature_differences_K": differences,
            "material_warning": "Synthetic material fixture only; no WCC cooling prediction.",
            "day_catalog": catalog}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annual", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("choose a new output; existing proof is not overwritten")
    result = verify(args.annual)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({k: v for k, v in result.items() if k != "day_catalog"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
