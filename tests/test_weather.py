import copy
import json
import math
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from src.geoscience.preprocess import FIELDS, transform
from src.geoscience.simulate import run
from src.geoscience.weather import (
    AIR_SPECIFIC_HEAT, accumulation_hours, attach_weather, dark_interval,
    longwave_estimate, moist_air_density, precipitation, prepare, solar_altitude, window,
)


def observations(start, hours):
    rows = []
    for i in range(hours):
        stamp = start + timedelta(hours=i)
        row = {"일시": stamp.strftime("%Y-%m-%d %H:%M")}
        for column, qc in FIELDS.values():
            row[column] = ""
            if qc:
                row[qc] = ""
        row.update({"기온(°C)": "25", "습도(%)": "60", "현지기압(hPa)": "1000",
                    "증기압(hPa)": "19", "풍속(m/s)": "2", "전운량(10분위)": "3",
                    "30cm 지중온도(°C)": "24", "일사(MJ/m2)": "0"})
        rows.append(row)
    return rows


class WeatherTests(unittest.TestCase):
    def test_noaa_geometry_and_full_interval(self):
        self.assertGreater(solar_altitude(datetime(2025, 6, 21, 12)), 70)
        self.assertLess(solar_altitude(datetime(2025, 6, 21, 0)), -20)
        self.assertTrue(dark_interval(datetime(2025, 1, 1, 7)))
        self.assertFalse(dark_interval(datetime(2025, 1, 1, 8)))
        self.assertFalse(dark_interval(datetime(2025, 7, 1, 20)))
        self.assertTrue(dark_interval(datetime(2025, 7, 1, 22)))

    def test_night_missing_fill_not_day_missing(self):
        raw = observations(datetime(2025, 7, 1, 0), 14)
        for row in raw:
            row["일사(MJ/m2)"] = ""
            row["일사 QC플래그"] = "9"
        rows = prepare(transform(raw), wind_factor=1, deep_boundary="observed_30cm")
        self.assertEqual(rows[1]["solar_w_m2"], 0)
        self.assertIsNotNone(rows[1]["forcing"])
        self.assertIsNone(rows[-1]["forcing"])
        self.assertIn("solar_missing_or_rejected", rows[-1]["blocking_reasons"])
        # QC9 plus a numeric value is a conflict even at night.
        raw[1]["일사(MJ/m2)"] = "0.1"
        rows = prepare(transform(raw), wind_factor=1, deep_boundary="observed_30cm")
        self.assertIsNone(rows[1]["forcing"])

    def test_rain_qc9_requires_independent_daily_total(self):
        raw = observations(datetime(2025, 7, 1, 1), 24)
        raw[0]["강수량(mm)"] = "2.3"
        raw[5]["강수량 QC플래그"] = "9"
        hourly = transform(raw)
        self.assertIsNone(precipitation(hourly)[datetime(2025, 7, 1, 6)]["mm"])
        daily = {"2025-07-01": {"rain_mm": 2.3}}
        result = precipitation(hourly, daily)
        self.assertEqual(sum(v["mm"] for v in result.values()), 2.3)
        self.assertEqual(result[datetime(2025, 7, 1, 6)]["reason"], "daily_total_reconciled_zero")
        # A mismatch blocks even apparently valid hours; no fabricated rain.
        daily["2025-07-01"]["rain_mm"] = 2.5
        self.assertTrue(all(v["mm"] is None for v in precipitation(hourly, daily).values()))

    def test_daily_identity_never_repairs_bad_numeric_or_error(self):
        raw = observations(datetime(2025, 7, 1, 1), 24)
        raw[5]["강수량(mm)"], raw[5]["강수량 QC플래그"] = "3", "9"
        daily = {"2025-07-01": {"rain_mm": 0}}
        result = precipitation(transform(raw), daily)
        self.assertIsNone(result[datetime(2025, 7, 1, 6)]["mm"])
        raw[5]["강수량(mm)"], raw[5]["강수량 QC플래그"] = "", "1"
        self.assertIsNone(precipitation(transform(raw), daily)[datetime(2025, 7, 1, 6)]["mm"])

    def test_winter_three_hour_total_and_calendar_boundary(self):
        raw = observations(datetime(2025, 1, 1, 1), 24)
        raw[2]["강수량(mm)"] = "3"
        for i in (0, 1):
            raw[i]["강수량 QC플래그"] = "9"  # not independent reporting slots
        result = precipitation(transform(raw))
        self.assertEqual([result[datetime(2025, 1, 1, h)]["mm"] for h in (1, 2, 3)], [1, 1, 1])
        self.assertEqual(sum(x["mm"] for x in result.values()), 3)
        self.assertEqual(accumulation_hours(datetime(2025, 4, 1, 0)), 3)
        self.assertEqual(accumulation_hours(datetime(2025, 4, 1, 1)), 1)
        self.assertEqual(accumulation_hours(datetime(2025, 11, 1, 0)), 1)
        self.assertEqual(accumulation_hours(datetime(2025, 11, 1, 1)), 3)

    def test_incomplete_daily_grid_not_reconciled(self):
        raw = observations(datetime(2025, 7, 1, 1), 23)
        raw[5]["강수량 QC플래그"] = "9"
        result = precipitation(transform(raw), {"2025-07-01": {"rain_mm": 0}})
        self.assertIsNone(result[datetime(2025, 7, 1, 6)]["mm"])

    def test_longwave_units_and_cloud_endpoints(self):
        # Independent hand example: 20 C, 10 hPa -> clear eps ~=0.765, L~320.
        clear = longwave_estimate(20, 1000, 0)
        cloudy = longwave_estimate(20, 1000, 1)
        self.assertAlmostEqual(clear, 1.24 * (10 / 293.15) ** (1 / 7) * 5.670374419e-8 * 293.15**4)
        self.assertGreater(cloudy, clear)
        with self.assertRaises(ValueError):
            longwave_estimate(20, 1000, 10)
        self.assertAlmostEqual(moist_air_density(20, 101325, 0), 1.204328, places=5)
        self.assertLess(moist_air_density(20, 101325, 1000), moist_air_density(20, 101325, 0))

    def test_endpoint_means_units_and_transfer(self):
        raw = observations(datetime(2025, 7, 1, 10), 2)
        raw[1]["기온(°C)"], raw[1]["일사(MJ/m2)"] = "27", "3.6"
        raw[1]["강수량(mm)"] = "1.8"
        row = prepare(transform(raw), wind_factor=.5, deep_boundary="observed_30cm")[1]
        forcing = row["forcing"]
        self.assertEqual(forcing["air_c"], 26)
        self.assertEqual(forcing["shortwave"], 1000)
        self.assertEqual(forcing["rain_rate"], 1.8 / 3600)
        self.assertEqual(forcing["convection_w_m2_k"], 9.5)
        self.assertEqual(forcing["aerodynamic_resistance_s_m"], forcing["air_density"] * AIR_SPECIFIC_HEAT / 9.5)

    def test_trace_snow_and_new_snow_back_propagation(self):
        raw = observations(datetime(2025, 1, 1, 0), 5)
        raw[3]["3시간신적설(cm)"] = "0"
        rows = prepare(transform(raw), wind_factor=1, deep_boundary="insulated")
        self.assertTrue(all("snow_reported" in rows[i]["blocking_reasons"] for i in (1, 2, 3)))
        self.assertNotIn("snow_reported", rows[4]["blocking_reasons"])

    def test_previous_endpoint_and_ground_missing(self):
        raw = observations(datetime(2025, 7, 1, 0), 3)
        raw[1]["30cm 지중온도(°C)"] = ""
        rows = prepare(transform(raw), wind_factor=1, deep_boundary="observed_30cm")
        self.assertIsNone(rows[0]["forcing"])
        self.assertIsNone(rows[1]["forcing"])
        self.assertIsNone(rows[2]["forcing"])
        self.assertIsNotNone(prepare(transform(raw), wind_factor=1, deep_boundary="insulated")[1]["forcing"])

    def test_snowfall_without_measurable_snow_depth_is_excluded(self):
        raw = observations(datetime(2025, 1, 1, 0), 4)
        raw[1]["현상번호(국내식)"] = "5"
        raw[2]["현상번호(국내식)"] = "190501"
        rows = prepare(transform(raw), wind_factor=1, deep_boundary="insulated")
        for i in (1, 2):
            self.assertIn("frozen_precipitation_reported", rows[i]["blocking_reasons"])
            self.assertIsNone(rows[i]["forcing"])

    def test_unexpected_winter_hourly_rain_is_not_dropped(self):
        raw = observations(datetime(2025, 1, 1, 0), 4)
        raw[1]["강수량(mm)"] = "1"
        with self.assertRaises(ValueError):
            precipitation(transform(raw))

    def test_winter_nonreporting_error_blocks_the_containing_accumulation(self):
        for qc in ("1", "7"):
            raw = observations(datetime(2025, 1, 1, 1), 24)
            raw[0]["강수량 QC플래그"] = qc
            result = precipitation(transform(raw), {"2025-01-01": {"rain_mm": 0}})
            for hour in (1, 2, 3):
                value = result[datetime(2025, 1, 1, hour)]
                self.assertIsNone(value["mm"])
                self.assertEqual(value["reason"], "rejected_nonreporting_qc")

    def test_solver_connection_and_schedule_preservation(self):
        start = datetime(2025, 7, 1, 0)
        raw = observations(start, 3)
        rows = prepare(transform(raw), wind_factor=1, deep_boundary="observed_30cm")
        bundle = {"hourly": rows, "source": "synthetic UNIT TEST fixture", "station": "112",
                  "timezone": "Asia/Seoul", "assumptions": {"wind_factor": 1},
                  "daily_source": None, "warning": "not observations"}
        job = json.loads(Path("examples/solver-smoke.json").read_text())
        with self.assertRaises(ValueError):
            attach_weather(job, bundle, start, 2)
        job.pop("intervals")
        before = copy.deepcopy(job)
        merged = attach_weather(job, bundle, start, 2)
        self.assertEqual(job, before)
        self.assertEqual(merged["weather_provenance"]["source"], bundle["source"])
        result = run(merged)
        self.assertLess(abs(result["whole_run_water_residual_kg_m2"]), 1e-9)
        self.assertLess(abs(result["whole_run_energy_residual_j_m2"]), 1e-6)
        with self.assertRaises(ValueError):
            window(rows, start, 24)


if __name__ == "__main__":
    unittest.main()
