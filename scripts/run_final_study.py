#!/usr/bin/env python3
"""Approved, resumable study runner. Physics stays in the reviewed solver.

Cache keys include complete solver jobs and all src/geoscience code hashes.
Only successful conservative integrations are cached. Outputs never overwrite.
"""
from __future__ import annotations
import argparse
import copy
import gzip
import hashlib
import json
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.geoscience.comparison import check_budget, metrics, summarize
from src.geoscience.model import OutsideModelDomain
from src.geoscience.preflight import digest, load_weather, variants, prepare_day, treatment
from src.geoscience.simulate import run
from src.geoscience.weather import TIME_FORMAT

BUNDLE = None
CACHE = None
CODE_HASHES = None


def encoded(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def write_gzip(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(f'.{os.getpid()}.tmp')
    temp.write_bytes(gzip.compress(encoded(obj), mtime=0))
    temp.replace(path)


def initialize(weather, cache, hashes):
    global BUNDLE, CACHE, CODE_HASHES
    BUNDLE = load_weather(weather)
    CACHE = Path(cache)
    CODE_HASHES = hashes


def cached_run(job, cfg):
    key = hashlib.sha256(encoded({'job': job, 'code': CODE_HASHES})).hexdigest()
    path = CACHE / key[:2] / (key + '.json.gz')
    if path.exists():
        result = load_weather(path)
        if result['input'] != job:
            raise ArithmeticError('cache input mismatch')
        check_budget(result, cfg)
    else:
        result = run(job, authorize_research=True)
        check_budget(result, cfg)
        write_gzip(path, result)
    return result


def worker(task):
    name, cfg, start_text, fractions = task
    start = datetime.strptime(start_text, TIME_FORMAT)
    phase = 'spinup'
    arm = None
    try:
        spinup, evaluation = prepare_day(BUNDLE, start, cfg)
        warm = cached_run(spinup, cfg)
        initial = warm['interval_results'][-1]['state']
        results = []
        phase = 'evaluation'
        for albedo in cfg['experiment']['albedos']:
            for fraction in fractions:
                arm = {'albedo': albedo, 'storage_fraction': fraction}
                job = treatment(cfg, evaluation, initial, albedo, fraction)
                result = cached_run(job, cfg)
                results.append({'treatment': job['treatment'], 'metrics': metrics(result),
                                'hourly_states': [initial] + [r['state'] for r in result['interval_results']],
                                'hourly_budgets': [r['budget'] for r in result['interval_results']]})
        return name, {'start_kst': start_text, 'initial_after_spinup': initial,
                      'spinup_metrics': {'budget': warm['total_budget'],
                         'water_residual_kg_m2': warm['whole_run_water_residual_kg_m2'],
                         'energy_residual_j_m2': warm['whole_run_energy_residual_j_m2']},
                      'results': results}, None
    except OutsideModelDomain as error:
        return name, None, {'date': start_text, 'reason': str(error), 'phase': phase,
                            'first_failing_arm': arm, 'all_arms_excluded': True}


def verified_inputs(config_path, prepared):
    cfg = json.loads(config_path.read_text())
    manifest = json.loads((prepared / 'manifest.json').read_text())
    if any(v == 'pending_user' for v in cfg['decisions'].values()):
        raise ValueError('unresolved research decision')
    if digest(config_path) != manifest['source_hashes']['config']:
        raise ValueError('config changed since preparation')
    for path, sha in manifest['code_hashes'].items():
        if digest(path) != sha:
            raise ValueError(f'code changed since preparation: {path}')
    weather = prepared / 'incheon2025-weather.json.gz'
    if digest(weather) != manifest['weather_bundle_sha256']:
        raise ValueError('weather bundle changed')
    return cfg, manifest, weather


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--approve-final-comparison', action='store_true')
    p.add_argument('--stage', choices=['convergence','initialization','coarse','sensitivity','refinement'], required=True)
    p.add_argument('--config', type=Path, default=Path('config/research-inputs.json'))
    p.add_argument('--prepared', type=Path, default=Path('data/preflight'))
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--cache', type=Path, default=Path('outputs/final-cache'))
    p.add_argument('--workers', type=int, default=4)
    args = p.parse_args()
    if not args.approve_final_comparison:
        p.error('explicit final comparison approval is required')
    if args.output.exists():
        p.error('refusing to overwrite a completed stage')
    cfg, manifest, weather = verified_inputs(args.config, args.prepared)
    if args.stage in ('coarse', 'sensitivity', 'refinement'):
        gate = json.loads(Path('results/gates.json').read_text())
        if not gate['numerical_gate_pass'] or gate['config_sha256'] != manifest['source_hashes']['config']:
            raise ValueError('matching, successful numerical gate required')
        for path, sha in gate['source_sha256'].items():
            if digest(path) != sha:
                raise ValueError('gate evidence changed')
    all_variants = variants(cfg)
    selected = {'baseline': cfg}
    if args.stage == 'sensitivity':
        selected = all_variants
    elif args.stage == 'initialization':
        selected = {k:v for k,v in all_variants.items() if k == 'baseline' or
                    v['experiment']['spinup_hours'] != cfg['experiment']['spinup_hours'] or
                    v['experiment']['initial_temperature_offset_k'] != cfg['experiment']['initial_temperature_offset_k']}
    elif args.stage == 'convergence':
        selected = {}
        for step in cfg['experiment']['convergence_steps_s']:
            v = copy.deepcopy(cfg)
            v['experiment']['max_step_s'] = step
            selected[f'step_{step}'] = v
    fractions = cfg['experiment']['refinement_fractions'] if args.stage == 'refinement' else cfg['experiment']['irrigation_storage_fractions']
    output = {'stage': args.stage, 'authorization': 'Explicit user approval in task on 2026-09-26; CLI approval supplied.',
              'started_utc': datetime.now(timezone.utc).isoformat(),
              'source_hashes': manifest['source_hashes'], 'weather_bundle_sha256': manifest['weather_bundle_sha256'],
              'code_hashes': manifest['code_hashes'], 'runner_sha256': digest(__file__),
              'config': cfg, 'variant_configs': selected, 'variants': {}, 'exclusions': {},
              'warning': 'Uncalibrated WCC-family/RCA proxy numerical comparison. No field or pedestrian-comfort validation.'}
    tasks = []
    for name, v in selected.items():
        ready_name = name if name in all_variants else 'baseline'
        output['variants'][name] = []
        output['exclusions'][name] = []
        for day in manifest['catalog']:
            readiness = day['variant_readiness'][ready_name]
            if not readiness['ready']:
                output['exclusions'][name].append({'date': day['start_kst'], 'phase': 'preflight', 'reason': readiness['reason'], 'all_arms_excluded': True})
            else:
                tasks.append((name, v, day['start_kst'], fractions))
    print(f'{args.stage}: {len(tasks)} date/variant jobs, {len(fractions)*3} arms each, workers={args.workers}', flush=True)
    with ProcessPoolExecutor(max_workers=args.workers, initializer=initialize,
          initargs=(str(weather), str(args.cache), manifest['code_hashes'])) as pool:
        futures = [pool.submit(worker, task) for task in tasks]
        for n, future in enumerate(as_completed(futures), 1):
            name, result, exclusion = future.result()  # Numerical/programming failures abort, never excluded silently.
            if result is not None:
                output['variants'][name].append(result)
            else:
                output['exclusions'][name].append(exclusion)
            if n % 25 == 0 or n == len(tasks):
                print(f'{args.stage}: {n}/{len(tasks)} completed', flush=True)
    for name in selected:
        output['variants'][name].sort(key=lambda d:d['start_kst'])
        output['exclusions'][name].sort(key=lambda d:d['date'])
    output['common_dates_across_variants'] = sorted(set.intersection(*[
        {d['start_kst'] for d in days} for days in output['variants'].values()]))
    output['paired_summary'] = summarize(output, cfg, manifest['catalog'])
    output['finished_utc'] = datetime.now(timezone.utc).isoformat()
    write_gzip(args.output, output)
    print(json.dumps({'output':str(args.output),'retained':{k:len(v) for k,v in output['variants'].items()},
                      'common':len(output['common_dates_across_variants'])}), flush=True)


if __name__ == '__main__':
    main()
