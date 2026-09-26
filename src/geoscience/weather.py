"""KMA hourly observations -> auditable, solver-compatible hourly forcing.

No WCC properties are supplied here. Estimated radiation/transfer coefficients
and temporal interpolation are labelled model assumptions, not observations.
See docs/weather-integration.md for sources and limitations.
"""

from __future__ import annotations

import argparse
import calendar
import copy
import json
import math
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from .model import Forcing
from .physics import STEFAN_BOLTZMANN, finite
from .preprocess import read_file, transform

TIME_FORMAT = "%Y-%m-%d %H:%M"
LATITUDE, LONGITUDE = 37.47772, 126.6249
AIR_GAS_CONSTANT, AIR_SPECIFIC_HEAT = 287.0, 1013.0


def solar_altitude(stamp: datetime) -> float:
    """NOAA approximate geometric solar altitude, Incheon, naive KST only."""
    if stamp.tzinfo is not None:
        raise ValueError("timestamps must be naive KST")
    hour = stamp.hour + stamp.minute / 60 + stamp.second / 3600
    days = 366 if calendar.isleap(stamp.year) else 365
    gamma = 2 * math.pi / days * (stamp.timetuple().tm_yday - 1 + (hour - 12) / 24)
    eq = 229.18 * (0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma)
                   - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma))
    decl = (0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma)
            - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma)
            - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma))
    hour_angle = math.radians((hour * 60 + eq + 4 * LONGITUDE - 60 * 9) / 4 - 180)
    latitude = math.radians(LATITUDE)
    sin_alt = (math.sin(latitude) * math.sin(decl)
               + math.cos(latitude) * math.cos(decl) * math.cos(hour_angle))
    return math.degrees(math.asin(max(-1, min(1, sin_alt))))


def dark_interval(end: datetime) -> bool:
    # Sample the WHOLE hour, not just its end. Horizon/twilight approximation,
    # not an assertion that all diffuse twilight radiation is physically zero.
    return max(solar_altitude(end - timedelta(minutes=i)) for i in range(61)) < -0.833


def moist_air_density(temp_c: float, pressure_pa: float, vapor_pa: float) -> float:
    finite("air temperature", temp_c)
    finite("pressure", pressure_pa, 0)
    finite("vapor pressure", vapor_pa, 0)
    if temp_c <= -273.15 or pressure_pa <= vapor_pa:
        raise ValueError("invalid atmospheric temperature/pressure")
    # Ideal mixture: equivalent to rho=p/(Rd*Tv), Tv=T/(1-0.378e/p).
    return (pressure_pa - 0.378 * vapor_pa) / (AIR_GAS_CONSTANT * (temp_c + 273.15))


def longwave_estimate(temp_c: float, vapor_pa: float, cloud_fraction: float) -> float:
    """Brutsaert clear sky + Unsworth/Monteith cloud correction (W/m²).

    1.24 requires vapor pressure in hPa and temperature in K. Not calibrated
    to Incheon. Outside admissible emissivity -> fail, never silently clamp.
    """
    finite("temperature", temp_c)
    finite("vapor pressure", vapor_pa, 0)
    finite("cloud fraction", cloud_fraction, 0)
    if temp_c <= -273.15 or cloud_fraction > 1:
        raise ValueError("invalid longwave input")
    kelvin = temp_c + 273.15
    clear = 1.24 * ((vapor_pa / 100) / kelvin) ** (1 / 7)
    if not 0 <= clear <= 1:
        raise ValueError("clear-sky emissivity outside [0,1]")
    sky = (1 - 0.84 * cloud_fraction) * clear + 0.84 * cloud_fraction
    return sky * STEFAN_BOLTZMANN * kelvin ** 4


def accumulation_hours(end: datetime) -> int:
    # Midnight's total belongs to the day that just ended, including Apr/Nov.
    return 3 if (end - timedelta(seconds=1)).month in (11, 12, 1, 2, 3) else 1


def precipitation(records: list[dict], daily: dict | None = None) -> dict:
    """Resolve reporting blocks, then conservatively distribute winter totals.

    Blank QC9 is resolved only by complete daily-total reconciliation. Nonblank
    rejected observations and QC1 are never repaired by this identity.
    """
    index = {datetime.strptime(row["time_kst"], TIME_FORMAT): row for row in records}
    blocks = {}
    groups = {}
    rejected_blocks = set()
    for end, row in index.items():
        hours = accumulation_hours(end)
        if hours == 3 and end.hour % 3:
            if row["rain_mm_raw"]:
                raise ValueError(f"unexpected winter rain outside 3h reporting grid: {end}")
            if row["rain_mm_status"] == "qc_error_or_other":
                rejected_blocks.add(end + timedelta(hours=(-end.hour) % 3))
            continue
        state, value = row["rain_mm_status"], row["rain_mm"]
        if value is not None:
            finite("precipitation", value, 0)
            reason = "observed_trace_or_zero" if value == 0 else "observed"
        elif state == "missing_unflagged":
            value, reason = 0.0, "kma_no_event_blank"
        else:
            reason = "unresolved_qc"
        day = (end - timedelta(seconds=1)).date().isoformat()
        blocks[end] = {"amount_mm": value, "hours": hours, "reason": reason, "day": day}
        groups.setdefault(day, []).append(end)
    for day, ends in groups.items():
        if daily is None or day not in daily or daily[day]["rain_mm"] is None:
            continue
        total = finite("daily precipitation", daily[day]["rain_mm"], 0)
        # Complete official reporting grid and no rejected numeric observation.
        hours = accumulation_hours(ends[0])
        expected = [datetime.fromisoformat(day) + timedelta(hours=h)
                    for h in range(hours, 25, hours)]
        if sorted(ends) != expected:
            continue
        invalid = any(index[e]["rain_mm_status"] not in (
            "qc_normal", "present_qc_blank", "missing_unflagged", "qc_missing"
        ) for e in ends)
        known = sum(blocks[e]["amount_mm"] or 0 for e in ends)
        if invalid:
            continue
        if abs(known - total) <= 1e-8:
            for end in ends:
                if blocks[end]["amount_mm"] is None:
                    blocks[end].update(amount_mm=0.0, reason="daily_total_reconciled_zero")
        else:
            # Could be missing rainfall or a time/measurement discrepancy. Do
            # not preserve a seemingly complete day with a known disagreement.
            for end in ends:
                blocks[end].update(amount_mm=None, reason="daily_total_mismatch")
    for end in rejected_blocks:
        if end in blocks:
            blocks[end].update(amount_mm=None, reason="rejected_nonreporting_qc")
    result = {}
    for end, row in index.items():
        hours = accumulation_hours(end)
        report_end = end if hours == 1 else end + timedelta(hours=(-end.hour) % 3)
        block = blocks.get(report_end)
        if block is None:
            result[end] = {"mm": None, "reason": "missing_reporting_block"}
            continue
        amount = block["amount_mm"]
        result[end] = {"mm": amount / hours if amount is not None else None,
                       "reason": block["reason"], "report_end_kst": report_end.strftime(TIME_FORMAT),
                       "reported_block_mm": amount, "block_hours": hours}
        if hours == 3:
            result[end]["allocation"] = "uniform_within_3h_assumption"
    return result


def prepare(records: list[dict], *, wind_factor: float, deep_boundary: str,
            daily: dict | None = None) -> list[dict]:
    """Prepare all rows; unavailable intervals retain specific blocking reasons.

    wind_factor is an EXPLICIT station-to-local wind scenario, not a measured
    correction. deep_boundary='observed_30cm' uses bare-soil observations as a
    boundary proxy, NOT as validation of the pavement's lower-layer temperature.
    """
    if finite("local wind factor", wind_factor, 0) == 0:
        raise ValueError("wind factor must be positive")
    if deep_boundary not in ("observed_30cm", "insulated"):
        raise ValueError("deep boundary must be observed_30cm or insulated")
    index = {datetime.strptime(row["time_kst"], TIME_FORMAT): row for row in records}
    rain = precipitation(records, daily)
    output = []
    states = ("temp_c", "pressure_pa", "vapor_pa", "wind_m_s", "cloud_fraction")
    for end, row in sorted(index.items()):
        previous = index.get(end - timedelta(hours=1))
        issues, notes = [], []
        sw, sw_reason = row["solar_w_m2"], row["solar_mj_m2_status"]
        # Reevaluate any legacy fixed-clock fill against the astronomical rule.
        if sw_reason == "assumed_dark_interval_zero":
            sw = None
        if sw is None and sw_reason in ("qc_missing", "missing_unflagged", "assumed_dark_interval_zero"):
            if dark_interval(end):
                sw, sw_reason = 0.0, "astronomical_night_zero_assumption"
        if sw is None:
            issues.append("solar_missing_or_rejected")
        else:
            finite("shortwave", sw, 0)
        if rain[end]["mm"] is None:
            issues.append("precipitation_" + rain[end]["reason"])
        if previous is None:
            issues.append("missing_previous_endpoint")
        averages = {}
        for name in states + (("ground_30cm_c",) if deep_boundary == "observed_30cm" else ()):
            values = [row.get(name), previous.get(name) if previous else None]
            if None in values:
                issues.append("missing_" + name)
            else:
                averages[name] = sum(values) / 2
        # Snow depth is instantaneous. Three-hour NEW snow denotes a preceding
        # interval: propagate an observed event backwards to each affected hour.
        # Numeric zero also reports snow: depth below the instrument resolution.
        snow = any(r.get("snow_cm") is not None for r in (row, previous or {}))
        for offset in range(3):
            report = index.get(end + timedelta(hours=offset), {})
            snow |= report.get("new_snow_cm") is not None
        if snow:
            issues.append("snow_reported")
        else:
            notes.append("no_reported_snow_not_proof_of_zero_patchy_snow")
        code = row.get("phenomena_code_raw", "")
        if code:
            if not code.isdigit() or len(code) > 6:
                issues.append("invalid_weather_phenomena_code")
            else:
                code = code.zfill(len(code) + len(code) % 2)
                codes = {int(code[i:i + 2]) for i in range(0, len(code), 2)}
                # KMA 2021 Table A-4: freezing rain/drizzle, snow, sleet,
                # hail/ice pellets and drifting/blowing snow. Past-hour events.
                if codes & ({3} | set(range(5, 16)) | {20, 21, 22}):
                    issues.append("frozen_precipitation_reported")
        forcing = None
        atmospheric = None
        if all(name in averages for name in states):
            temp, pressure, vapor = (averages[k] for k in ("temp_c", "pressure_pa", "vapor_pa"))
            local_wind = finite("wind", averages["wind_m_s"], 0) * wind_factor
            density = moist_air_density(temp, pressure, vapor)
            hc = 5.7 + 3.8 * local_wind
            atmospheric = {"longwave": longwave_estimate(temp, vapor, averages["cloud_fraction"]),
                           "air_density": density, "convection_w_m2_k": hc,
                           "aerodynamic_resistance_s_m": density * AIR_SPECIFIC_HEAT / hc}
            if rain[end]["mm"] is not None and rain[end]["mm"] > 0 and temp <= 0:
                issues.append("freezing_precipitation")
            if temp <= 0:
                notes.append("air_below_zero_wet_state_must_stop_in_solver")
            if not issues:
                forcing = asdict(Forcing(
                    air_c=temp, shortwave=sw, pressure_pa=pressure, vapor_pa=vapor,
                    deep_c=averages.get("ground_30cm_c"), **atmospheric,
                    rain_rate=rain[end]["mm"] / 3600, rain_c=temp,
                    irrigation_rate=0.0, irrigation_c=temp, snow_present=False,
                ))
        output.append({"end_kst": end.strftime(TIME_FORMAT),
                       "start_kst": (end - timedelta(hours=1)).strftime(TIME_FORMAT),
                       "duration_s": 3600, "forcing": forcing,
                       "blocking_reasons": issues, "notes": notes,
                       "precipitation": rain[end], "solar_status": sw_reason,
                       "solar_w_m2": sw, "atmospheric_estimates": atmospheric,
                       "observations": row})
    return output


def window(prepared: list[dict], start: datetime, hours: int = 24) -> list[dict]:
    if hours <= 0 or start.minute or start.second or start.microsecond or start.tzinfo:
        raise ValueError("window needs positive whole hours and a naive KST hourly start")
    index = {row["end_kst"]: row for row in prepared}
    result = []
    for offset in range(1, hours + 1):
        key = (start + timedelta(hours=offset)).strftime(TIME_FORMAT)
        row = index.get(key)
        if row is None or row["forcing"] is None:
            reasons = row["blocking_reasons"] if row else ["missing_timestamp"]
            raise ValueError(f"unusable weather interval {key}: {reasons}")
        result.append(copy.deepcopy(row))
    return result


def attach_weather(material_job: dict, weather_bundle: dict, start: datetime,
                   hours: int = 24) -> dict:
    """Connect to simulate.run, preserving supplied initial/material conditions.

    No inferred initial pavement temperatures or moisture. Irrigation remains
    zero: this adapter cannot overwrite a supplied irrigation experiment.
    """
    if material_job.get("intervals"):
        raise ValueError("use a material job without intervals; refusing to replace a schedule")
    result = copy.deepcopy(material_job)
    result["intervals"] = window(weather_bundle["hourly"], start, hours)
    result["weather_provenance"] = {k: copy.deepcopy(weather_bundle[k]) for k in (
        "source", "station", "timezone", "assumptions", "daily_source", "warning"
    )}
    return result


def day_catalog(prepared: list[dict]) -> list[dict]:
    index = {row["end_kst"]: row for row in prepared}
    result = []
    for month in (1, 2, 7, 8):
        for day in range(1, calendar.monthrange(2025, month)[1] + 1):
            start = datetime(2025, month, day, 6)
            rows = [index.get((start + timedelta(hours=h)).strftime(TIME_FORMAT)) for h in range(1, 25)]
            issues = Counter(reason for row in rows for reason in (
                row["blocking_reasons"] if row else ["missing_timestamp"]))
            complete_solar = all(row and row["solar_w_m2"] is not None for row in rows)
            complete_rain = all(row and row["precipitation"]["mm"] is not None for row in rows)
            result.append({"start_kst": start.strftime(TIME_FORMAT),
                           "weather_ready": not issues, "blocking_reasons": dict(issues),
                           "wet_freezing_risk_from_air": any(
                               row and row["observations"]["temp_c"] is not None
                               and row["observations"]["temp_c"] <= 0 for row in rows),
                           "rain_mm": sum(row["precipitation"]["mm"] for row in rows) if complete_rain else None,
                           "solar_mj_m2": sum(row["solar_w_m2"] * 0.0036 for row in rows) if complete_solar else None})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annual", type=Path, required=True)
    parser.add_argument("--daily", type=Path)
    parser.add_argument("--wind-factor", type=float, required=True)
    parser.add_argument("--deep-boundary", choices=("observed_30cm", "insulated"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error(f"output already exists: {args.output}")
    raw, metadata = read_file(args.annual)
    if not raw:
        parser.error("no station 112 observations")
    daily_source = json.loads(args.daily.read_text(encoding="utf-8")) if args.daily else None
    if daily_source and (str(daily_source["station"]) != "112" or daily_source["year"] != 2025):
        parser.error("daily data must be Incheon 112, 2025")
    hourly = prepare(transform(raw), wind_factor=args.wind_factor, deep_boundary=args.deep_boundary,
                     daily=daily_source["days"] if daily_source else None)
    catalog = day_catalog(hourly)
    result = {"source": metadata, "station": "112", "timezone": "Asia/Seoul",
              "daily_source": daily_source,
              "assumptions": {"wind_factor": args.wind_factor, "deep_boundary": args.deep_boundary,
                              "states": "arithmetic_mean_of_two_hourly_endpoints",
                              "longwave": "Brutsaert1975_UnsworthMonteith1975_uncalibrated",
                              "convection": "McAdams_5.7+3.8_local_wind",
                              "mass_transfer": "ra=rho*1013/hc; equal heat/vapor transfer assumption",
                              "irrigation": "none; supply initial water in separate material experiment"},
              "summary": {"hours": len(hourly),
                          "ready_hours": sum(row["forcing"] is not None for row in hourly),
                          "candidate_days": len(catalog),
                          "weather_ready_days": sum(row["weather_ready"] for row in catalog)},
              "warning": "Weather-ready is not WCC-material-ready or experimentally validated.",
              "day_catalog": catalog, "hourly": hourly}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
