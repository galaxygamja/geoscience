"""SI-unit building blocks; no fitted WCC material coefficients are hidden here.

Sources: FAO-56 ch. 3 eq. 11 (saturation pressure), NIST CODATA (sigma),
and docs/concrete-model-draft.md T1-T3/E1/W2-W3 (two-node reduction).
"""

from __future__ import annotations

import math

STEFAN_BOLTZMANN = 5.670374419e-8  # W / (m² K⁴), NIST CODATA
LATENT_HEAT = 2.45e6  # J/kg, FAO-56 constant approximation near 20 °C
WATER_HEAT_CAPACITY = 4174.0  # J/(kg K), Dong 2019 Table 2, nominal 40 °C


def finite(name: str, value: float, lower: float | None = None) -> float:
    if not math.isfinite(value) or (lower is not None and value < lower):
        raise ValueError(f"{name}: invalid value {value!r}")
    return value


def saturation_pressure_pa(temp_c: float) -> float:
    """FAO-56 eq. 11, liquid-water saturation; not an ice formula."""
    finite("temperature", temp_c)
    if not -50 <= temp_c <= 80:
        raise ValueError("saturation-pressure helper restricted to -50..80 °C")
    return 610.8 * math.exp(17.27 * temp_c / (temp_c + 237.3))


def specific_humidity(vapor_pa: float, pressure_pa: float) -> float:
    """Ideal moist-air mass fraction using molecular-mass ratio 0.622."""
    finite("vapor pressure", vapor_pa, 0)
    finite("air pressure", pressure_pa, 0)
    if vapor_pa >= pressure_pa:
        raise ValueError("vapor pressure must be below positive total pressure")
    return 0.622 * vapor_pa / (pressure_pa - 0.378 * vapor_pa)


def evaporation_potential(
    temp_c: float, vapor_pa: float, pressure_pa: float, air_density: float,
    aerodynamic_resistance: float, surface_resistance: float,
) -> float:
    """E1, kg/(m² s), positive evaporation; condensation excluded explicitly.

    Both resistances are s/m. Infinite surface resistance represents no
    evaporation. The caller must supply a material-specific resistance law.
    """
    finite("air density", air_density, 0)
    finite("aerodynamic resistance", aerodynamic_resistance, 0)
    if math.isnan(surface_resistance) or surface_resistance < 0:
        raise ValueError("surface resistance must be nonnegative")
    if aerodynamic_resistance <= 0:
        raise ValueError("aerodynamic resistance must be positive")
    qa = specific_humidity(vapor_pa, pressure_pa)
    qs = specific_humidity(saturation_pressure_pa(temp_c), pressure_pa)
    return air_density * max(qs - qa, 0.0) / (
        aerodynamic_resistance + surface_resistance
    )


def darcy_flux(
    surface_k_m_s: float, base_k_m_s: float,
    surface_head_m: float, base_head_m: float,
    surface_depth_m: float, base_depth_m: float, water_density: float,
) -> float:
    """W2-W3, kg/(m² s); positive down, negative up, z positive down.

    Heads are pressure heads (negative in suction), NOT positive suction
    magnitudes. K values are hydraulic conductivity, not thermal conductivity.
    This helper supplies no retention curves or interface calibration.
    """
    for name, value in (("Ks", surface_k_m_s), ("Kb", base_k_m_s)):
        finite(name, value, 0)
    for name, value in (("ds", surface_depth_m), ("db", base_depth_m),
                        ("water density", water_density)):
        finite(name, value, 0)
        if value == 0:
            raise ValueError(f"{name} must be positive")
    finite("surface head", surface_head_m)
    finite("base head", base_head_m)
    if surface_k_m_s == 0 or base_k_m_s == 0:
        return 0.0
    distance = (surface_depth_m + base_depth_m) / 2
    resistance = surface_depth_m / (2 * surface_k_m_s) + base_depth_m / (2 * base_k_m_s)
    return water_density * (distance + surface_head_m - base_head_m) / resistance
