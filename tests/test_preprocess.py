import unittest
from datetime import datetime

from src.geoscience.preprocess import catalog, number, status, transform


class PreprocessTests(unittest.TestCase):
    def test_qc_blank_is_not_qc_zero(self):
        self.assertEqual(status("", ""), "missing_unflagged")
        self.assertEqual(status("2.3", ""), "present_qc_blank")
        self.assertEqual(status("2.3", "9"), "qc_missing_value_conflict")
        self.assertIsNone(number("2.3", status("2.3", "9")))

    def test_period_ending_window(self):
        rows = []
        for hour in range(31):
            t = datetime(2025, 7, 1, 0) + __import__("datetime").timedelta(hours=hour)
            rows.append({"time_kst": t.strftime("%Y-%m-%d %H:%M"), "rain_mm": 1.0 if hour == 30 else None,
                         "rain_mm_status": "present_qc_blank" if hour == 30 else "missing_unflagged",
                         "solar_mj_m2": 0.0, "temp_c": 20.0, "snow_cm": None, "new_snow_cm": None})
        first = next(x for x in catalog(rows) if x["date_kst"] == "2025-07-01")
        self.assertEqual(first["interval_rows"], 24)
        self.assertEqual(first["rain_positive_mm_lower_bound"], 1.0)


if __name__ == "__main__":
    unittest.main()
