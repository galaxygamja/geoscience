import copy
import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from src.geoscience.model import Forcing, State
from src.geoscience.physics import evaporation_potential
from src.geoscience.preflight import material_job, variants, prepare_day, treatment
from src.geoscience.simulate import components, run
from src.geoscience.comparison import run_day, choose_water

ROOT=Path(__file__).resolve().parents[1]


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.config=json.loads((ROOT/'config/research-inputs.json').read_text())
        self.synthetic=json.loads((ROOT/'examples/solver-smoke.json').read_text())

    def test_capacity_reference_not_double_counted(self):
        job=material_job(self.config)
        p=job['parameters'];c=job['capacity_conversion']
        self.assertAlmostEqual(p['surface']['dry_capacity_j_m2_k']+p['water_heat_capacity']*c['assumed_reference_water_kg_m2'],75000)
        self.assertAlmostEqual(p['base']['dry_capacity_j_m2_k'],387100)
        self.assertAlmostEqual(p['base']['max_water_kg_m2'],65)
        for cfg in variants(self.config).values():
            self.assertGreater(material_job(cfg)['parameters']['surface']['dry_capacity_j_m2_k'],0)

    def test_research_lock_precedes_integration(self):
        mock=Mock(side_effect=AssertionError('must never integrate'))
        with patch('src.geoscience.simulate.advance',mock):
            with self.assertRaises(PermissionError):run(material_job(self.config))
        with self.assertRaises(PermissionError):
            run_day(self.config,{},datetime(2025,7,1,6),[0,0.5,1],integrator=mock)
        mock.assert_not_called()

    def test_beta_transfer_identity(self):
        job=material_job(self.config);p,exchange,factory=components(job)
        f=Forcing(**self.synthetic['intervals'][0]['forcing'])
        full=p.surface.max_water_kg_m2
        potential=evaporation_potential(30,f.vapor_pa,f.pressure_pa,f.air_density,f.aerodynamic_resistance_s_m,0)
        for fraction in (0.25,0.5,1):
            actual=evaporation_potential(30,f.vapor_pa,f.pressure_pa,f.air_density,f.aerodynamic_resistance_s_m,factory(f)(full*fraction))
            self.assertAlmostEqual(actual/potential,0.04*fraction)
        self.assertEqual(factory(f)(0),float('inf'))

    def test_bucket_sign_and_max_rates(self):
        p,exchange,_=components(material_job(self.config))
        down=exchange(State(20,20,p.surface.max_water_kg_m2,0))*3600
        up=exchange(State(20,20,0,p.base.max_water_kg_m2))*3600
        self.assertAlmostEqual(down,1)
        self.assertAlmostEqual(up,-0.1)
        self.assertAlmostEqual(exchange(State(20,20,p.surface.max_water_kg_m2/2,p.base.max_water_kg_m2/2)),0)

    def test_initial_conditions_schedule_and_no_solver_call(self):
        # Synthetic weather only: builds warmup/evaluation, never integrates.
        start=datetime(2025,7,1,6);rows=[]
        forcing=copy.deepcopy(self.synthetic['intervals'][0]['forcing'])
        forcing['rain_rate']=0
        for offset in range(-71,25):
            end=start+timedelta(hours=offset)
            rows.append({'end_kst':end.strftime('%Y-%m-%d %H:%M'),'duration_s':3600,'forcing':copy.deepcopy(forcing),'blocking_reasons':[]})
        bundle={'hourly':rows,'assumptions':{'wind_factor':1}}
        with patch('src.geoscience.simulate.advance',side_effect=AssertionError('no integration')):
            warm,evaluation=prepare_day(bundle,start,self.config)
        self.assertEqual(len(warm['intervals']),72)
        self.assertEqual(len(evaluation),24)
        state=warm['initial_state'];job=treatment(self.config,evaluation,state,0.5,1)
        self.assertEqual(job['initial_state'],state)
        self.assertAlmostEqual(sum(r['duration_s']*r['forcing']['irrigation_rate'] for r in job['intervals']),5.95)
        self.assertEqual(evaluation[0]['forcing']['irrigation_rate'],0)
        self.assertAlmostEqual(job['intervals'][0]['forcing']['rain_rate'],0)

    def test_optimum_nonpositive_and_smallest_95_percent(self):
        self.assertIsNone(choose_water({0:[0],1:[-1]},[0.95])['minimum_water_at_threshold']['0.95'])
        answer=choose_water({0:[0,0],1:[9.5,9.5],2:[10,10]},[0.95,0.9])
        self.assertEqual(answer['minimum_water_at_threshold']['0.95'],1)
        with self.assertRaises(ValueError):choose_water({0:[0],1:[1,2]},[0.95])
        with self.assertRaises(ValueError):choose_water({0:[]},[0.95])

if __name__=='__main__':unittest.main()
