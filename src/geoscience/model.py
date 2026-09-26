"""Conservative four-state, two-layer heat/water kernel on one square metre.

This is an executable numerical model, NOT a calibrated WCC material preset.
All material properties, atmospheric transfer coefficients and water laws
must be supplied. See docs/concrete-model-draft.md for the physical equations.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Callable

from .physics import (
    LATENT_HEAT, STEFAN_BOLTZMANN, WATER_HEAT_CAPACITY,
    evaporation_potential, finite,
)


class OutsideModelDomain(ValueError):
    """Wet freezing/snow or another explicitly excluded physical regime."""


@dataclass(frozen=True)
class Layer:
    depth_m: float
    dry_capacity_j_m2_k: float
    max_water_kg_m2: float
    # A scalar for a constant-k experiment, or k(W) for a supported wet/dry law.
    conductivity: float | Callable[[float], float]

    def __post_init__(self):
        for name in ("depth_m", "dry_capacity_j_m2_k", "max_water_kg_m2"):
            finite(name, getattr(self, name), 0)
            if getattr(self, name) == 0:
                raise ValueError(f"{name} must be positive")
        self.k(0.0)

    def k(self, water: float) -> float:
        value = self.conductivity(water) if callable(self.conductivity) else self.conductivity
        finite("thermal conductivity", value, 0)
        if value == 0:
            raise ValueError("thermal conductivity must be positive")
        return value


@dataclass(frozen=True)
class Parameters:
    surface: Layer
    base: Layer
    albedo: float
    emissivity: float
    drainage_kg_m2_s: float
    allow_recharge: bool
    water_heat_capacity: float = WATER_HEAT_CAPACITY
    latent_heat: float = LATENT_HEAT

    def __post_init__(self):
        for name in ("albedo", "emissivity"):
            value = finite(name, getattr(self, name), 0)
            if value > 1:
                raise ValueError(f"{name} must be in [0, 1]")
        finite("drainage", self.drainage_kg_m2_s, 0)
        for name in ("water_heat_capacity", "latent_heat"):
            if finite(name, getattr(self, name), 0) == 0:
                raise ValueError(f"{name} must be positive")


@dataclass(frozen=True)
class State:
    surface_c: float
    base_c: float
    surface_water: float
    base_water: float


@dataclass(frozen=True)
class Forcing:
    """Constant over one forcing interval. Rates are kg/(m² s), heat W/m².

    No missing-observation-to-zero conversion occurs in this class. Longwave,
    convection and aerodynamic resistance are explicit inputs, not silently
    estimated from uncorrected station wind. deep_c=None means insulation.
    """
    air_c: float
    shortwave: float
    longwave: float
    deep_c: float | None
    pressure_pa: float
    vapor_pa: float
    air_density: float
    convection_w_m2_k: float
    aerodynamic_resistance_s_m: float
    rain_rate: float
    rain_c: float
    irrigation_rate: float
    irrigation_c: float
    snow_present: bool

    def __post_init__(self):
        for name in ("shortwave", "longwave", "pressure_pa", "vapor_pa", "air_density",
                     "convection_w_m2_k", "rain_rate", "irrigation_rate"):
            finite(name, getattr(self, name), 0)
        for name in ("air_c", "rain_c", "irrigation_c"):
            finite(name, getattr(self, name))
            if getattr(self, name) <= -273.15:
                raise ValueError(f"{name} must exceed absolute zero")
        if self.deep_c is not None:
            finite("deep_c", self.deep_c)
            if self.deep_c <= -273.15:
                raise ValueError("deep_c must exceed absolute zero")
        if self.pressure_pa <= self.vapor_pa or self.air_density <= 0:
            raise ValueError("invalid air density or vapor/total pressure")
        if finite("air resistance", self.aerodynamic_resistance_s_m, 0) == 0:
            raise ValueError("aerodynamic resistance must be positive")
        if self.snow_present:
            raise OutsideModelDomain("snow is outside this model")
        if (self.rain_rate > 0 and self.rain_c <= 0) or (
            self.irrigation_rate > 0 and self.irrigation_c <= 0
        ):
            raise OutsideModelDomain("freezing incoming water is outside this model")


@dataclass(frozen=True)
class Budget:
    """Integrated amounts on this step/interval, never unlabelled rates."""
    rain_kg_m2: float = 0.0
    irrigation_kg_m2: float = 0.0
    evaporated_kg_m2: float = 0.0
    downward_kg_m2: float = 0.0
    upward_kg_m2: float = 0.0
    drained_kg_m2: float = 0.0
    runoff_kg_m2: float = 0.0
    absorbed_shortwave_j_m2: float = 0.0
    net_longwave_j_m2: float = 0.0
    sensible_out_j_m2: float = 0.0
    latent_out_j_m2: float = 0.0
    ground_out_j_m2: float = 0.0
    water_enthalpy_net_j_m2: float = 0.0
    water_residual_kg_m2: float = 0.0
    energy_residual_j_m2: float = 0.0

    def __add__(self, other: Budget) -> Budget:
        return Budget(**{f.name: getattr(self, f.name) + getattr(other, f.name) for f in fields(self)})


def validate_state(state: State, params: Parameters) -> None:
    for temp, water, layer in (
        (state.surface_c, state.surface_water, params.surface),
        (state.base_c, state.base_water, params.base),
    ):
        finite("temperature", temp)
        if temp <= -273.15:
            raise ValueError("temperature below absolute zero")
        finite("stored water", water, 0)
        if water > layer.max_water_kg_m2 + 1e-10:
            raise ValueError("water exceeds storage capacity")
        if water > 0 and temp <= 0:
            raise OutsideModelDomain("wet layer at or below 0 °C; no temperature clamping")


def energy(state: State, params: Parameters) -> float:
    """Stored sensible enthalpy relative to 0 °C, J/m², both layers."""
    cw = params.water_heat_capacity
    return ((params.surface.dry_capacity_j_m2_k + cw * state.surface_water) * state.surface_c
            + (params.base.dry_capacity_j_m2_k + cw * state.base_water) * state.base_c)


def step(
    state: State, forcing: Forcing, params: Parameters, dt_s: float,
    *, evaporation_kg_m2_s: float, exchange_kg_m2_s: float,
) -> tuple[State, Budget]:
    """One explicit finite-volume step with conservative limited water fluxes.

    Exchange is positive down. Competing outflows share the donor's available
    water proportionally; receiving capacity also limits exchange. New inflow
    joins storage before the next step's evaporation calculation. Excess surface
    water runs off at the post-mixing temperature. Nothing is discarded by an
    unrecorded state clip. Call advance() for temporal subdivision.
    """
    validate_state(state, params)
    if finite("dt", dt_s, 0) == 0:
        raise ValueError("dt must be positive")
    finite("evaporation", evaporation_kg_m2_s, 0)
    finite("exchange", exchange_kg_m2_s)
    ts, tb, ws, wb = state.surface_c, state.base_c, state.surface_water, state.base_water
    cw = params.water_heat_capacity
    es = evaporation_kg_m2_s * dt_s
    down = max(exchange_kg_m2_s, 0.0) * dt_s
    up = max(-exchange_kg_m2_s, 0.0) * dt_s if params.allow_recharge else 0.0
    drain = params.drainage_kg_m2_s * dt_s
    # Proportional sharing prevents evaporation/drainage from double spending.
    surface_out = es + down
    if surface_out > ws:
        es, down = es * ws / surface_out, down * ws / surface_out
    base_out = up + drain
    if base_out > wb:
        up, drain = up * wb / base_out, drain * wb / base_out
    down = min(down, max(0.0, params.base.max_water_kg_m2 - wb + up + drain))
    up = min(up, max(0.0, params.surface.max_water_kg_m2 - ws + es + down))
    rain = forcing.rain_rate * dt_s
    irrigation = forcing.irrigation_rate * dt_s
    ws_next = ws + rain + irrigation + up - es - down
    wb_next = wb + down - up - drain
    # Only roundoff is normalized; macroscopic deficits are programming errors.
    if min(ws_next, wb_next) < -1e-10:
        raise ArithmeticError("negative water after flux limiting")
    ws_next, wb_next = max(ws_next, 0.0), max(wb_next, 0.0)
    ks, kb = params.surface.k(ws), params.base.k(wb)
    resistance = params.surface.depth_m / (2 * ks) + params.base.depth_m / (2 * kb)
    conductive = (ts - tb) / resistance * dt_s
    ground = 0.0 if forcing.deep_c is None else (tb - forcing.deep_c) * 2 * kb / params.base.depth_m * dt_s
    sw = (1 - params.albedo) * forcing.shortwave * dt_s
    lw = params.emissivity * (forcing.longwave - STEFAN_BOLTZMANN * (ts + 273.15)**4) * dt_s
    sensible = forcing.convection_w_m2_k * (ts - forcing.air_c) * dt_s
    latent = params.latent_heat * es
    hs = ((params.surface.dry_capacity_j_m2_k + cw * ws) * ts
          + sw + lw - sensible - latent - conductive
          + cw * (rain * forcing.rain_c + irrigation * forcing.irrigation_c
                  + up * tb - (es + down) * ts))
    hb = ((params.base.dry_capacity_j_m2_k + cw * wb) * tb
          + conductive - ground + cw * (down * ts - (up + drain) * tb))
    ts_next = hs / (params.surface.dry_capacity_j_m2_k + cw * ws_next)
    tb_next = hb / (params.base.dry_capacity_j_m2_k + cw * wb_next)
    runoff = max(ws_next - params.surface.max_water_kg_m2, 0.0)
    ws_next -= runoff
    # Removing liquid at its own mixed T does not independently cool the node.
    result = State(ts_next, tb_next, ws_next, wb_next)
    for old_temp, new_temp, old_water, new_water in (
        (ts, ts_next, ws, ws_next), (tb, tb_next, wb, wb_next)
    ):
        if (old_water > 0 or new_water > 0) and min(old_temp, new_temp) <= 0:
            raise OutsideModelDomain("wet freezing crossed; interval cannot be used")
    validate_state(result, params)
    water_enthalpy = cw * (rain * forcing.rain_c + irrigation * forcing.irrigation_c
                           - es * ts - drain * tb - runoff * ts_next)
    mass_expected = rain + irrigation - es - drain - runoff
    heat_expected = sw + lw - sensible - latent - ground + water_enthalpy
    budget = Budget(rain, irrigation, es, down, up, drain, runoff, sw, lw, sensible,
                    latent, ground, water_enthalpy,
                    ws_next + wb_next - ws - wb - mass_expected,
                    energy(result, params) - energy(state, params) - heat_expected)
    return result, budget


def advance(
    state: State, forcing: Forcing, params: Parameters, duration_s: float,
    *, surface_resistance: Callable[[float], float],
    exchange: Callable[[State], float], max_step_s: float = 10.0,
) -> tuple[State, Budget]:
    """Advance a forcing interval; recompute evaporation and exchange each step.

    First-order conservative integration. A passive-heat stability restriction
    complements max_step_s; convergence must be checked by halving max_step_s.
    No weather interpolation or material-curve fitting is performed here.
    """
    finite("duration", duration_s, 0)
    if finite("maximum step", max_step_s, 0) == 0:
        raise ValueError("maximum step must be positive")
    validate_state(state, params)
    remaining, budget = duration_s, Budget()
    while remaining > 1e-9:
        cw = params.water_heat_capacity
        ks, kb = params.surface.k(state.surface_water), params.base.k(state.base_water)
        g = 1 / (params.surface.depth_m / (2 * ks) + params.base.depth_m / (2 * kb))
        g_bottom = 0.0 if forcing.deep_c is None else 2 * kb / params.base.depth_m
        radiation_slope = 4 * params.emissivity * STEFAN_BOLTZMANN * (state.surface_c + 273.15)**3
        stable_s = 0.25 * (params.surface.dry_capacity_j_m2_k + cw * state.surface_water) / (
            g + radiation_slope + forcing.convection_w_m2_k
        )
        stable_b = 0.25 * (params.base.dry_capacity_j_m2_k + cw * state.base_water) / (g + g_bottom)
        dt = min(remaining, max_step_s, stable_s, stable_b)
        evap = 0.0 if state.surface_water == 0 else evaporation_potential(
            state.surface_c, forcing.vapor_pa, forcing.pressure_pa, forcing.air_density,
            forcing.aerodynamic_resistance_s_m, surface_resistance(state.surface_water),
        )
        transfer = exchange(state)
        # Resolve cooling from stiff evaporation rather than permitting a giant
        # explicit temperature jump. 0.25 K is a numerical cap, not a fitted law.
        dt = min(dt, 0.25 * (params.surface.dry_capacity_j_m2_k + cw * state.surface_water)
                 / max(params.latent_heat * evap, 1e-30))
        if dt <= 0 or remaining - dt == remaining:
            raise ArithmeticError("time step too small to advance; check supplied material laws")
        next_state, delta = step(state, forcing, params, dt,
                                 evaporation_kg_m2_s=evap, exchange_kg_m2_s=transfer)
        state, budget = next_state, budget + delta
        remaining -= dt
    return state, budget
