"""Audit KMA hourly ASOS inputs without silently turning missing data into zero."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

FIELDS = {
    "temp_c": ("기온(°C)", "기온 QC플래그"),
    "rain_mm": ("강수량(mm)", "강수량 QC플래그"),
    "wind_m_s": ("풍속(m/s)", "풍속 QC플래그"),
    "rh_pct": ("습도(%)", "습도 QC플래그"),
    "vapor_hpa": ("증기압(hPa)", None),
    "pressure_hpa": ("현지기압(hPa)", "현지기압 QC플래그"),
    "solar_mj_m2": ("일사(MJ/m2)", "일사 QC플래그"),
    "cloud_tenths": ("전운량(10분위)", None),
    "snow_cm": ("적설(cm)", None),
    "new_snow_cm": ("3시간신적설(cm)", None),
    "ground_30cm_c": ("30cm 지중온도(°C)", None),
}


def status(raw: str, qc: str | None) -> str:
    if qc == "9":
        return "qc_missing_value_conflict" if raw != "" else "qc_missing"
    if qc not in (None, "", "0"):
        return "qc_error_or_other"
    if raw == "":
        return "missing_unflagged"
    return "qc_normal" if qc == "0" else "present_qc_blank"


def number(raw: str, state: str) -> float | None:
    return float(raw) if raw and state in ("qc_normal", "present_qc_blank") else None


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_file(path: Path) -> tuple[list[dict], dict]:
    records = []
    stations = Counter()
    times = []
    with path.open(newline="", encoding="cp949") as file:
        reader = csv.DictReader(file)
        for row in reader:
            stations[row["지점"]] += 1
            times.append(row["일시"])
            if row["지점"] == "112":
                records.append(row)
    return records, {
        "filename": path.name,
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "encoding": "cp949",
        "rows": sum(stations.values()),
        "station_rows": dict(stations),
        "first_timestamp": min(times),
        "last_timestamp": max(times),
    }


def transform(rows: list[dict]) -> list[dict]:
    output = []
    seen = set()
    for row in rows:
        stamp = datetime.strptime(row["일시"], "%Y-%m-%d %H:%M")
        if stamp in seen:
            raise ValueError(f"duplicate Incheon timestamp: {stamp}")
        seen.add(stamp)
        item = {"time_kst": stamp.strftime("%Y-%m-%d %H:%M")}
        for name, (column, qc_column) in FIELDS.items():
            raw = row[column].strip()
            qc = row[qc_column].strip() if qc_column else None
            state = status(raw, qc)
            item[name] = number(raw, state)
            item[f"{name}_raw"] = raw
            item[f"{name}_qc"] = qc or "" if qc_column else "not_provided"
            item[f"{name}_status"] = state
        # A packed string of two-digit domestic weather codes, not a number.
        item["phenomena_code_raw"] = row.get("현상번호(국내식)", "").strip()
        # Only intervals ending at 00-04 or 21-23 are safely dark all year.
        if item["solar_mj_m2"] is None and stamp.hour in (0, 1, 2, 3, 4, 21, 22, 23):
            if item["solar_mj_m2_status"] == "qc_missing":
                item["solar_mj_m2"] = 0.0
                item["solar_mj_m2_status"] = "assumed_dark_interval_zero"
        item["temp_k"] = item["temp_c"] + 273.15 if item["temp_c"] is not None else None
        item["rh_fraction"] = item["rh_pct"] / 100 if item["rh_pct"] is not None else None
        item["pressure_pa"] = item["pressure_hpa"] * 100 if item["pressure_hpa"] is not None else None
        item["vapor_pa"] = item["vapor_hpa"] * 100 if item["vapor_hpa"] is not None else None
        item["cloud_fraction"] = item["cloud_tenths"] / 10 if item["cloud_tenths"] is not None else None
        item["solar_w_m2"] = item["solar_mj_m2"] * 1e6 / 3600 if item["solar_mj_m2"] is not None else None
        output.append(item)
    output.sort(key=lambda row: row["time_kst"])
    for earlier, later in zip(output, output[1:]):
        delta = datetime.strptime(later["time_kst"], "%Y-%m-%d %H:%M") - datetime.strptime(earlier["time_kst"], "%Y-%m-%d %H:%M")
        if delta != timedelta(hours=1):
            raise ValueError(f"hourly gap: {earlier['time_kst']} -> {later['time_kst']}")
    return output


def compare_season(annual: list[dict], seasonal: list[dict]) -> dict:
    annual_index = {row["일시"]: row for row in annual}
    names = ["기온(°C)", "강수량(mm)", "풍속(m/s)", "습도(%)", "일사(MJ/m2)"]
    overlap = 0
    mismatches = []
    for row in seasonal:
        other = annual_index.get(row["일시"])
        if other is None:
            continue
        overlap += 1
        for name in names:
            if row[name].strip() != other[name].strip():
                mismatches.append([row["일시"], name, row[name], other[name]])
    return {"overlap_rows_incheon": overlap, "mismatch_count": len(mismatches), "mismatch_examples": mismatches[:10]}


def catalog(records: list[dict]) -> list[dict]:
    index = {datetime.strptime(row["time_kst"], "%Y-%m-%d %H:%M"): row for row in records}
    dates = [datetime(2025, month, day, 6) for month, days in ((1, 31), (2, 28), (7, 31), (8, 31)) for day in range(1, days + 1)]
    result = []
    for start in dates:
        # KMA interval totals are treated provisionally as period-ending values.
        hours = [index.get(start + timedelta(hours=h)) for h in range(1, 25)]
        available = [row for row in hours if row is not None]
        rain = [row["rain_mm"] for row in available if row["rain_mm"] is not None]
        solar = [row["solar_mj_m2"] for row in available if row["solar_mj_m2"] is not None]
        temps = [row["temp_c"] for row in available if row["temp_c"] is not None]
        rain_unknown = sum(row["rain_mm"] is None for row in available)
        rain_qc9 = sum(row["rain_mm_status"].startswith("qc_missing") for row in available)
        snow_evidence = any((row["snow_cm"] or 0) > 0 or (row["new_snow_cm"] or 0) > 0 for row in available)
        result.append({
            "date_kst": start.date().isoformat(),
            "season": "winter" if start.month < 3 else "summer",
            "window_start_kst": start.strftime("%Y-%m-%d %H:%M"),
            "window_end_kst": (start + timedelta(days=1)).strftime("%Y-%m-%d %H:%M"),
            "interval_rows": len(available),
            "temp_rows": len(temps),
            "solar_rows": len(solar),
            "solar_mj_m2_sum_observed": round(sum(solar), 4),
            "temp_min_c": min(temps) if temps else "",
            "temp_max_c": max(temps) if temps else "",
            "rain_positive_mm_lower_bound": round(sum(x for x in rain if x > 0), 4),
            "rain_unknown_hours": rain_unknown,
            "rain_qc9_hours": rain_qc9,
            "rain_status": "observed_positive" if any(x > 0 for x in rain) else ("unresolved_missing" if rain_unknown or len(available) < 24 else "zero_observed"),
            "snow_status": "observed_positive" if snow_evidence else "unresolved_no_positive_record",
            "full_meteorology": len(available) == 24 and len(temps) == 24 and len(solar) == 24,
        })
    return result


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--annual", type=Path, required=True)
    parser.add_argument("--winter", type=Path, required=True)
    parser.add_argument("--summer", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("outputs/preprocessed"))
    parser.add_argument("--manifest", type=Path, default=Path("data/source_manifest.json"))
    args = parser.parse_args()
    annual, annual_meta = read_file(args.annual)
    winter, winter_meta = read_file(args.winter)
    summer, summer_meta = read_file(args.summer)
    winter_compare = compare_season(annual, winter)
    summer_compare = compare_season(annual, summer)
    if winter_compare["mismatch_count"] or summer_compare["mismatch_count"]:
        raise ValueError("seasonal and annual overlapping observations differ")
    hourly = transform(annual)
    dates = catalog(hourly)
    write_csv(args.out / "incheon_2025_hourly.csv", hourly)
    write_csv(args.out / "day_catalog.csv", dates)
    manifest = {
        "source": "Korea Meteorological Administration ASOS hourly observations; user-provided exports",
        "station": "112 Incheon",
        "files": [annual_meta, winter_meta, summer_meta],
        "overlap_checks": {"winter": winter_compare, "summer": summer_compare},
        "processing": "cp949 -> UTF-8; QC-preserving; KST; period-ending convention provisional",
        "day_catalog_summary": {
            "candidate_days": len(dates),
            "full_meteorology_days": sum(row["full_meteorology"] for row in dates),
            "observed_positive_rain_days": sum(row["rain_status"] == "observed_positive" for row in dates),
            "unresolved_rain_days": sum(row["rain_status"] == "unresolved_missing" for row in dates),
            "snow_positive_days": sum(row["snow_status"] == "observed_positive" for row in dates),
        },
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest["day_catalog_summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
