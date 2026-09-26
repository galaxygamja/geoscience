import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts.check_final_gates import compare
from scripts import run_final_study as runner
from src.geoscience.model import OutsideModelDomain


def day(date, dry=20, wet=10, offset=0):
    return {'start_kst':date,'initial_after_spinup':{'surface_c':25+offset},'results':[
        {'treatment':{'albedo':.3,'storage_fraction':fraction},
         'metrics':{'air_excess_degree_hours':value},
         'hourly_states':[{'surface_c':25+offset} for _ in range(25)]}
        for fraction,value in ((0,dry),(1,wet))]}


class FinalStudyTests(unittest.TestCase):
    def test_gate_pairs_dates_not_positions_and_effect_is_control_relative(self):
        left=[day('a'),day('b',30,20)]
        right=[day('b',31,22,.01),day('a',21,12,.02)]
        result=compare(left,right)
        self.assertEqual(result['compared_hourly_temperatures'],100)
        self.assertAlmostEqual(result['maximum_surface_difference_k'],.02)
        self.assertEqual(result['maximum_watering_effect_difference_k_h'],1)
        self.assertEqual(result['worst_temperature']['date'],'a')

    def test_gate_rejects_mismatched_grid_duplicate_dates_and_records_empty(self):
        malformed=day('a'); malformed['results'].pop()
        with self.assertRaises(ValueError):compare([day('a')],[malformed])
        with self.assertRaises(ValueError):compare([day('a'),day('a')],[day('a')])
        result=compare([day('a')],[day('b')])
        self.assertEqual(result['compared_hourly_temperatures'],0)
        self.assertIsNone(result['maximum_surface_difference_k'])
        self.assertEqual(result['left_only_dates'],['a'])

    def test_domain_failure_excludes_whole_date_numerical_error_aborts(self):
        task=('baseline',{},'2025-07-03 06:00',[0,.5,1])
        with patch.object(runner,'prepare_day',side_effect=OutsideModelDomain('freezing')):
            name,result,error=runner.worker(task)
        self.assertIsNone(result); self.assertTrue(error['all_arms_excluded'])
        with patch.object(runner,'prepare_day',side_effect=ArithmeticError('failed conservation')):
            with self.assertRaises(ArithmeticError):runner.worker(task)

    def test_unsuccessful_run_never_cached(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(runner,'CACHE',Path(folder)), patch.object(runner,'CODE_HASHES',{}), \
                    patch.object(runner,'run',return_value={'input':{}}), \
                    patch.object(runner,'check_budget',side_effect=ArithmeticError('bad')):
                with self.assertRaises(ArithmeticError):runner.cached_run({}, {})
            self.assertEqual(list(Path(folder).rglob('*.gz')),[])

if __name__ == '__main__':unittest.main()
