import copy
import json
import unittest
from pathlib import Path

from src.geoscience.simulate import run, table_function


class RunTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "examples" / "solver-smoke.json"
        with path.open() as stream:
            self.job = json.load(stream)

    def test_curve(self):
        curve = table_function([[0., 100.], [3., 40.], [6., 0.]])
        self.assertEqual(curve(0), 100.)
        self.assertEqual(curve(1.5), 70.)
        self.assertEqual(curve(6), 0.)
        for x in (-1., 7., float("nan")):
            with self.assertRaises(ValueError):
                curve(x)
        for rows in ([], [[1, 2]], [[1, 2], [1, 3]], [[0, 2], [1, float("nan")]]):
            with self.assertRaises(ValueError):
                table_function(rows)

    def test_smoke_run(self):
        result = run(self.job)
        self.assertIn("NOT WCC", result["label"])
        self.assertEqual(len(result["interval_results"]), 2)
        self.assertAlmostEqual(result["whole_run_water_residual_kg_m2"], 0., places=10)
        self.assertAlmostEqual(result["whole_run_energy_residual_j_m2"], 0., places=5)
        self.assertEqual(result["input"], self.job)

    def test_no_recharge_comparison(self):
        job = copy.deepcopy(self.job)
        job["initial_state"]["surface_water"] = 1.
        job["initial_state"]["base_water"] = 15.
        result = run(job)
        self.assertGreater(result["total_budget"]["upward_kg_m2"], 0.)
        job["parameters"]["allow_recharge"] = False
        result = run(job)
        self.assertEqual(result["total_budget"]["upward_kg_m2"], 0.)

    def test_missing_forcing_is_not_zero_filled(self):
        del self.job["intervals"][0]["forcing"]["rain_rate"]
        with self.assertRaises(TypeError):
            run(self.job)

    def test_variable_conductivity(self):
        self.job["parameters"]["surface"]["conductivity"] = [[0, 1.], [6, 1.4]]
        result = run(self.job)
        self.assertAlmostEqual(result["whole_run_energy_residual_j_m2"], 0., places=5)


if __name__ == "__main__":
    unittest.main()
