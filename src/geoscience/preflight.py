"""Prepare and validate research inputs WITHOUT invoking time integration."""
from __future__ import annotations

import copy
import gzip
import hashlib
import json
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from .model import Forcing, State, validate_state
from .simulate import components
from .weather import TIME_FORMAT, window


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_weather(path):
    with gzip.open(path, "rt", encoding="utf-8") if str(path).endswith('.gz') else open(path, encoding="utf-8") as f:
        return json.load(f)


def variants(config):
    result = {"baseline": copy.deepcopy(config)}
    for item in config["sensitivity"]:
        for i, value in enumerate(item["values"]):
            variant = copy.deepcopy(config)
            keys = item["path"].split('.')
            target = variant
            for key in keys[:-1]:
                target = target[key]
            if keys[-1] not in target:
                raise ValueError(f"unknown sensitivity path {item['path']}")
            target[keys[-1]] = value
            result[f"{item['id']}_{i+1}"] = variant
    return result


def material_job(config):
    """Explicit dry-equivalent conversion; never label it a measured dry value."""
    s, b, w, c = (config[k] for k in ("surface", "base", "water", "constants"))
    t = s["thermal_pairs"][s["thermal_pair"]]
    reference_water = s["storage_kg_m2"] * s["reference_water_fraction"]
    if not 0 <= s["reference_water_fraction"] <= 1 or not 0 <= b["initial_water_fraction"] <= 1:
        raise ValueError("reference/initial water fraction outside 0..1")
    job = {
        "research_run": True,
        "label": config["label"],
        "evidence_note": "Proxy ensemble; different specimens and explicit assumptions. No WCC calibration. See config/research-inputs.json and docs/preflight-input-evidence.md.",
        "max_step_s": config["experiment"]["max_step_s"],
        "parameters": {
            "surface": {"depth_m": s["depth_m"],
                        "dry_capacity_j_m2_k": t["effective_capacity_j_m3_k"] * s["depth_m"] - c["water_heat_capacity_j_kg_k"] * reference_water,
                        "max_water_kg_m2": s["storage_kg_m2"], "conductivity": t["conductivity_w_m_k"]},
            "base": {"depth_m": b["depth_m"], "dry_capacity_j_m2_k": b["dry_density_kg_m3"] * b["dry_specific_heat_j_kg_k"] * b["depth_m"],
                     "max_water_kg_m2": b["porosity"] * b["depth_m"] * c["water_density_kg_m3"], "conductivity": b["conductivity_w_m_k"]},
            "albedo": config["experiment"]["spinup_albedo"], "emissivity": s["emissivity"],
            "drainage_kg_m2_s": w["drainage_mm_h"] / 3600, "allow_recharge": True,
            "water_heat_capacity": c["water_heat_capacity_j_kg_k"], "latent_heat": c["latent_heat_j_kg"]},
        "exchange": {"mode": w["exchange_mode"], "downward_max_mm_h": w["downward_max_mm_h"], "upward_max_mm_h": w["upward_max_mm_h"]},
        "evaporation": {"mode": "beta", "beta_max": s["beta_max"], "shape": s["beta_shape"]},
        "capacity_conversion": {"effective_surface_capacity_j_m2_k": t["effective_capacity_j_m3_k"] * s["depth_m"],
                                "assumed_reference_water_kg_m2": reference_water},
    }
    components(job)
    return job


def forcing_rows(bundle, start, hours, config):
    rows = window(bundle['hourly'], start, hours)
    old_wind = bundle['assumptions']['wind_factor']
    new_wind = config['weather']['wind_factor']
    if old_wind <= 0 or new_wind <= 0:
        raise ValueError('wind factors must be positive')
    for row in rows:
        f = row['forcing']
        f['convection_w_m2_k'] = 5.7 + (f['convection_w_m2_k'] - 5.7) * new_wind / old_wind
        f['aerodynamic_resistance_s_m'] = f['air_density'] * 1013 / f['convection_w_m2_k']
        f['longwave'] += config['weather']['longwave_offset_w_m2']
        if config['weather']['deep_boundary'] == 'insulated':
            f['deep_c'] = None
        elif config['weather']['deep_boundary'] != 'observed_30cm' or f['deep_c'] is None:
            raise ValueError('missing observed 30 cm lower boundary')
        Forcing(**f)
    return rows


def prepare_day(bundle, start, config):
    """Return spin-up job and future treatments; no temperature is calculated."""
    ex = config['experiment']
    spinup = material_job(config)
    first = start - timedelta(hours=ex['spinup_hours'])
    spinup['intervals'] = forcing_rows(bundle, first, ex['spinup_hours'], config)
    f = spinup['intervals'][0]['forcing']
    # Even insulated case retains a bare-soil temperature only as an initial proxy.
    observed = window(bundle['hourly'], first, 1)[0]['forcing']
    delta = ex['initial_temperature_offset_k']
    spinup['initial_state'] = {
        'surface_c': f['air_c'] + delta,
        'base_c': observed['deep_c'] + delta,
        'surface_water': 0.0,
        'base_water': config['base']['initial_water_fraction'] * spinup['parameters']['base']['max_water_kg_m2'],
    }
    validate_state(State(**spinup['initial_state']), components(spinup)[0])
    evaluation = forcing_rows(bundle, start, ex['hours'], config)
    if ex['irrigation_duration_s'] != 3600 or ex['hours'] != 24:
        raise ValueError('this protocol supports 06-07 irrigation and a 24h evaluation')
    if evaluation[0]['forcing']['air_c'] <= 0:
        raise ValueError('06-07 air-temperature water proxy is at/below freezing')
    return spinup, evaluation


def treatment(config, evaluation, initial_state, albedo, fraction):
    job = material_job(config)
    job['parameters']['albedo'] = albedo
    job['initial_state'] = copy.deepcopy(initial_state)
    job['intervals'] = copy.deepcopy(evaluation)
    dose = fraction * config['surface']['storage_kg_m2']
    job['intervals'][0]['forcing']['irrigation_rate'] = dose / config['experiment']['irrigation_duration_s']
    job['treatment'] = {'albedo': albedo, 'delivered_water_kg_m2': dose, 'storage_fraction': fraction}
    return job


def audit(config, bundle):
    if bundle['station'] != '112' or bundle['timezone'] != 'Asia/Seoul':
        raise ValueError('wrong station/timezone')
    keys = [r['end_kst'] for r in bundle['hourly']]
    if len(keys) != len(set(keys)) or keys != sorted(keys):
        raise ValueError('duplicate or unsorted weather timestamps')
    if config['execution']['final_comparison_authorized'] is not False:
        raise ValueError('preflight must retain final comparison lock')
    variant_configs = variants(config)
    for value in variant_configs.values():
        material_job(value)
    catalog = []
    for day in bundle['day_catalog']:
        entry = copy.deepcopy(day)
        entry['variant_readiness'] = {}
        start = datetime.strptime(day['start_kst'], TIME_FORMAT)
        for name, v in variant_configs.items():
            try:
                spinup, rows = prepare_day(bundle, start, v)
                # Check all factorial forcings & initial state types without stepping.
                for a in v['experiment']['albedos']:
                    for fraction in v['experiment']['irrigation_storage_fractions']:
                        candidate = treatment(v, rows, spinup['initial_state'], a, fraction)
                        components(candidate)
                        Forcing(**candidate['intervals'][0]['forcing'])
                entry['variant_readiness'][name] = {'ready': True}
            except ValueError as error:
                entry['variant_readiness'][name] = {'ready': False, 'reason': str(error)}
        entry['baseline_preflight_ready'] = entry['variant_readiness']['baseline']['ready']
        entry['cohort'] = ('reported_rain' if (entry['rain_mm'] or 0) > 0 else 'zero_recorded_amount')
        catalog.append(entry)
    by_month = {}
    for month in (1,2,7,8):
        days = [d for d in catalog if int(d['start_kst'][5:7]) == month]
        by_month[str(month)] = {'candidate':len(days), 'weather_ready':sum(d['weather_ready'] for d in days),
                              'preflight_ready':sum(d['baseline_preflight_ready'] for d in days)}
    return {'status':'prepared_not_executed', 'final_comparison_authorized':False,
            'scope_decisions':config['decisions'], 'variants':list(variant_configs),
            'counts_by_month':by_month, 'catalog':catalog,
            'runtime_limits':'Wet freezing, spin-up adequacy and material sensitivity remain runtime checks after approval, never proof of WCC validity.'}
