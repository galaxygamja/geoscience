"""Manufactured cases test the solver, not the performance of real WCC."""

import math
import random
import unittest
from dataclasses import replace

from src.geoscience.model import (
    Forcing, Layer, OutsideModelDomain, Parameters, State, advance, energy, step,
)
from src.geoscience.physics import darcy_flux, evaporation_potential, saturation_pressure_pa, specific_humidity


def fixture_params(**changes):
    # Intentionally round synthetic coefficients, NOT literature WCC properties.
    return replace(Parameters(Layer(.05, 80000., 6., 1.), Layer(.25, 300000., 20., 1.),
                              .3, 0., 0., True), **changes)


def fixture_forcing(**changes):
    return replace(Forcing(20., 0., 0., None, 101325., 1000., 1.2, 0., 100.,
                           0., 20., 0., 20., False), **changes)


class PhysicsTests(unittest.TestCase):
    def test_fao_published_examples(self):
        self.assertAlmostEqual(saturation_pressure_pa(24.5) / 1000, 3.075, places=3)
        self.assertAlmostEqual(saturation_pressure_pa(15) / 1000, 1.705, places=3)

    def test_specific_humidity(self):
        self.assertAlmostEqual(specific_humidity(1000, 100000), .0062436, places=7)
        self.assertEqual(specific_humidity(0, 100000), 0)
        for e, p in ((1000, 1000), (1001, 1000), (-1, 1000), (0, 0)):
            with self.assertRaises(ValueError):
                specific_humidity(e, p)

    def test_evaporation_resistance_and_no_condensation(self):
        args = (30., 1000., 101325., 1.2, 100.)
        wet = evaporation_potential(*args, 0.)
        self.assertGreater(wet, 0)
        self.assertAlmostEqual(evaporation_potential(*args, 100.), wet / 2)
        self.assertEqual(evaporation_potential(*args, math.inf), 0.)
        self.assertEqual(evaporation_potential(20., 3000., 101325., 1.2, 100., 0.), 0.)
        with self.assertRaises(ValueError):
            evaporation_potential(*args, float("nan"))

    def test_darcy_directions_and_units(self):
        # Homogeneous K and equal pressure heads -> rho*K under gravity.
        self.assertAlmostEqual(darcy_flux(1e-7, 1e-7, -.2, -.2, .05, .25, 1000.), 1e-4)
        self.assertAlmostEqual(darcy_flux(1e-7, 1e-7, -.1, -.4, .05, .25, 1000.), 3e-4)
        self.assertAlmostEqual(darcy_flux(1e-7, 1e-7, -1., -.2, .05, .25, 1000.), -4.333333333e-4)
        self.assertEqual(darcy_flux(0., 1e-7, -1., -.2, .05, .25, 1000.), 0.)
        self.assertAlmostEqual(darcy_flux(1e-7, 1e-7, -.35, -.2, .05, .25, 1000.), 0.)
        with self.assertRaises(ValueError):
            darcy_flux(-1., 1e-7, -1., -.2, .05, .25, 1000.)


class ConservationTests(unittest.TestCase):
    def setUp(self):
        self.p, self.f = fixture_params(), fixture_forcing()

    def run_step(self, state, dt=10., e=0., j=0., **kwargs):
        return step(state, kwargs.get("forcing", self.f), kwargs.get("params", self.p), dt,
                    evaporation_kg_m2_s=e, exchange_kg_m2_s=j)

    def test_equilibrium(self):
        initial = State(20., 20., 3., 2.)
        result, budget = self.run_step(initial)
        self.assertEqual(result, initial)
        self.assertEqual(budget.energy_residual_j_m2, 0.)

    def test_example_water_ledger(self):
        p = replace(self.p, drainage_kg_m2_s=.1 / 3600)
        result, b = self.run_step(State(20., 20., 3., 2.), dt=3600., e=.4 / 3600,
                                  j=-.05 / 3600, params=p)
        self.assertAlmostEqual(result.surface_water, 2.65)
        self.assertAlmostEqual(result.base_water, 1.85)
        self.assertAlmostEqual(b.water_residual_kg_m2, 0.)
        self.assertAlmostEqual(b.energy_residual_j_m2, 0., places=6)

    def test_lower_water_cannot_evaporate_directly(self):
        result, b = self.run_step(State(20., 20., 0., 5.), e=1.)
        self.assertEqual(result.surface_water, 0.)
        self.assertEqual(result.base_water, 5.)
        self.assertEqual(b.evaporated_kg_m2, 0.)

    def test_recharge_arrives_before_next_evaporation_step(self):
        result, b = self.run_step(State(20., 20., 0., 5.), e=1., j=-.01)
        self.assertEqual(b.evaporated_kg_m2, 0.)
        self.assertAlmostEqual(result.surface_water, .1)
        _, b2 = self.run_step(result, e=.005)
        self.assertGreater(b2.evaporated_kg_m2, 0.)

    def test_surface_competing_outflows(self):
        result, b = self.run_step(State(30., 30., .01, 1.), e=.005, j=.005)
        self.assertAlmostEqual(b.evaporated_kg_m2 + b.downward_kg_m2, .01)
        self.assertAlmostEqual(b.evaporated_kg_m2, .005)
        self.assertEqual(result.surface_water, 0.)

    def test_base_competing_outflows(self):
        p = replace(self.p, drainage_kg_m2_s=.01)
        result, b = self.run_step(State(20., 20., 1., .03), j=-.02, params=p)
        self.assertAlmostEqual(b.upward_kg_m2 + b.drained_kg_m2, .03)
        self.assertAlmostEqual(b.upward_kg_m2, .02)
        self.assertAlmostEqual(result.base_water, 0.)

    def test_drainage_stops_without_cooling_by_itself(self):
        p = replace(self.p, drainage_kg_m2_s=1.)
        result, b = self.run_step(State(20., 20., 0., .03), params=p)
        self.assertEqual(result.base_water, 0.)
        self.assertAlmostEqual(result.base_c, 20.)
        self.assertAlmostEqual(b.drained_kg_m2, .03)
        _, empty = self.run_step(result, params=p)
        self.assertEqual(empty.drained_kg_m2, 0.)

    def test_constant_drain_not_percentage(self):
        p = replace(self.p, drainage_kg_m2_s=.01)
        for water in (1., 10.):
            _, b = self.run_step(State(20., 20., 0., water), params=p)
            self.assertAlmostEqual(b.drained_kg_m2, .1)

    def test_receiver_capacity(self):
        result, b = self.run_step(State(20., 20., 3., 20.), j=1.)
        self.assertEqual(b.downward_kg_m2, 0.)
        self.assertEqual(result.surface_water, 3.)
        result, b = self.run_step(State(20., 20., 6., 3.), j=-1.)
        self.assertEqual(b.upward_kg_m2, 0.)
        self.assertEqual(result.base_water, 3.)

    def test_no_recharge_does_not_disable_downward_flow(self):
        p = replace(self.p, allow_recharge=False)
        initial = State(20., 20., 3., 3.)
        _, b = self.run_step(initial, j=-.01, params=p)
        self.assertEqual(b.upward_kg_m2, 0.)
        _, b = self.run_step(initial, j=.01, params=p)
        self.assertGreater(b.downward_kg_m2, 0.)

    def test_evaporative_energy_is_not_double_counted(self):
        initial = State(30., 30., 3., 0.)
        result, b = self.run_step(initial, e=.01)
        expected = 30. - self.p.latent_heat * .1 / (80000. + self.p.water_heat_capacity * 2.9)
        self.assertAlmostEqual(result.surface_c, expected)
        self.assertAlmostEqual(b.latent_out_j_m2, 245000.)

    def test_cold_irrigation_mixing_and_runoff_enthalpy(self):
        initial = State(30., 30., 5., 0.)
        result, b = self.run_step(initial, forcing=fixture_forcing(irrigation_rate=.3, irrigation_c=10.))
        cw = self.p.water_heat_capacity
        expected = ((80000. + cw * 5) * 30 + cw * 3 * 10) / (80000. + cw * 8)
        self.assertAlmostEqual(result.surface_c, expected)
        self.assertEqual(result.surface_water, 6.)
        self.assertEqual(b.runoff_kg_m2, 2.)
        self.assertAlmostEqual(b.energy_residual_j_m2, 0., places=6)

    def test_1000_random_mass_and_energy_ledgers(self):
        rng = random.Random(260926)
        for _ in range(1000):
            state = State(rng.uniform(15., 45.), rng.uniform(15., 45.),
                          rng.uniform(0., 6.), rng.uniform(0., 20.))
            p = replace(self.p, emissivity=.94, drainage_kg_m2_s=rng.uniform(0., .01))
            f = fixture_forcing(shortwave=rng.uniform(0., 1000.), longwave=350.,
                                deep_c=20., convection_w_m2_k=10., rain_rate=.01,
                                irrigation_rate=.01, irrigation_c=15.)
            result, b = self.run_step(state, dt=2., e=rng.uniform(0., .001),
                                      j=rng.uniform(-.02, .02), params=p, forcing=f)
            self.assertLess(abs(b.water_residual_kg_m2), 1e-12)
            self.assertLess(abs(b.energy_residual_j_m2), 1e-7)
            self.assertLessEqual(result.surface_water, 6.)
            self.assertLessEqual(result.base_water, 20. + 1e-10)

    def test_freezing_is_rejected_not_clamped(self):
        with self.assertRaises(OutsideModelDomain):
            self.run_step(State(-1., 2., 1., 0.))
        with self.assertRaises(OutsideModelDomain):
            self.run_step(State(.01, .01, 1., 0.), e=.01)
        with self.assertRaises(OutsideModelDomain):
            self.run_step(State(-1., 2., 0., 0.), forcing=fixture_forcing(irrigation_rate=.01))
        result, _ = self.run_step(State(-1., -1., 0., 0.))
        self.assertEqual(result.surface_c, -1.)

    def test_bad_input(self):
        for dt in (0, -1, float("nan")):
            with self.assertRaises(ValueError):
                self.run_step(State(20., 20., 0., 0.), dt=dt)
        with self.assertRaises(ValueError):
            self.run_step(State(20., 20., 7., 0.))
        with self.assertRaises(OutsideModelDomain):
            fixture_forcing(snow_present=True)
        with self.assertRaises(ValueError):
            fixture_forcing(shortwave=float("nan"))
        with self.assertRaises(ValueError):
            fixture_params(albedo=1.1)


class IntegrationTests(unittest.TestCase):
    def test_closed_heat_exchange_analytical_solution_and_convergence(self):
        p, f = fixture_params(), fixture_forcing()
        start = State(40., 20., 0., 0.)
        duration = 3600.
        # Independently derived exact solution for two isolated heat reservoirs.
        mean = (80000 * 40 + 300000 * 20) / 380000
        g = 1 / (.025 / 1. + .125 / 1.)
        difference = 20. * math.exp(-g * (1 / 80000 + 1 / 300000) * duration)
        exact = mean + 300000 / 380000 * difference
        errors = []
        for dt in (60., 30., 15.):
            result, b = advance(start, f, p, duration, surface_resistance=lambda w: math.inf,
                                 exchange=lambda s: 0., max_step_s=dt)
            errors.append(abs(result.surface_c - exact))
            self.assertAlmostEqual(energy(result, p), energy(start, p), places=5)
            self.assertLess(abs(b.energy_residual_j_m2), 1e-6)
        self.assertLess(errors[2], .01)
        self.assertGreater(errors[0] / errors[1], 1.9)
        self.assertGreater(errors[1] / errors[2], 1.9)

    def test_coupled_wet_case_converges(self):
        p = fixture_params(emissivity=.94, drainage_kg_m2_s=.05 / 3600)
        f = fixture_forcing(air_c=30., shortwave=700., longwave=400., convection_w_m2_k=10.)
        results = []
        for dt in (20., 10., 5.):
            state, b = advance(State(30., 25., 3., 5.), f, p, 7200.,
                                surface_resistance=lambda w: 100. / max(w, .001),
                                exchange=lambda s: -.02 / 3600., max_step_s=dt)
            results.append(state)
            self.assertLess(abs(b.water_residual_kg_m2), 1e-10)
            self.assertLess(abs(b.energy_residual_j_m2), 1e-5)
        self.assertLess(abs(results[1].surface_c - results[2].surface_c), .01)
        self.assertLess(abs(results[1].surface_c - results[2].surface_c),
                        abs(results[0].surface_c - results[1].surface_c))

    def test_split_forcing_intervals(self):
        p, f = fixture_params(), fixture_forcing(shortwave=500.)
        state = State(20., 20., 0., 0.)
        opts = dict(surface_resistance=lambda w: math.inf, exchange=lambda s: 0., max_step_s=10.)
        whole, _ = advance(state, f, p, 3600., **opts)
        first, _ = advance(state, f, p, 1800., **opts)
        second, _ = advance(first, f, p, 1800., **opts)
        self.assertEqual(whole, second)


if __name__ == "__main__":
    unittest.main()
