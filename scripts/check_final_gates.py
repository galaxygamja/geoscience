#!/usr/bin/env python3
"""Compare approved integrations; never integrate or alter model parameters."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.geoscience.preflight import load_weather, digest


def arms(day):
    return {(r['treatment']['albedo'], r['treatment']['storage_fraction']):r for r in day['results']}


def compare(left, right):
    l = {d['start_kst']:d for d in left}
    r = {d['start_kst']:d for d in right}
    if len(l) != len(left) or len(r) != len(right):
        raise ValueError('duplicate comparison date')
    common = sorted(l.keys() & r.keys())
    max_t = max_a = 0.0
    worst_t = worst_a = None
    count = 0
    days = []
    for date in common:
        la, ra = arms(l[date]), arms(r[date])
        if la.keys() != ra.keys():
            raise ValueError('comparison treatment grids differ')
        dmax_t = dmax_a = 0.0
        for key in la.keys() & ra.keys():
            x, y = la[key], ra[key]
            diffs = [abs(s['surface_c']-t['surface_c']) for s,t in zip(x['hourly_states'], y['hourly_states'])]
            if len(diffs) != 25:
                raise ValueError('comparison requires identical 25 hourly endpoint states')
            count += len(diffs)
            delta_t = max(diffs)
            base = (key[0], 0)
            le = la[base]['metrics']['air_excess_degree_hours'] - x['metrics']['air_excess_degree_hours']
            re = ra[base]['metrics']['air_excess_degree_hours'] - y['metrics']['air_excess_degree_hours']
            delta_a = abs(le-re)
            if delta_t > max_t:
                max_t = delta_t; worst_t = {'date':date, 'albedo':key[0], 'fraction':key[1], 'endpoint_hour':diffs.index(delta_t)}
            if delta_a > max_a:
                max_a = delta_a; worst_a = {'date':date, 'albedo':key[0], 'fraction':key[1]}
            dmax_t = max(dmax_t,delta_t); dmax_a = max(dmax_a,delta_a)
        days.append({'date':date,'maximum_surface_difference_k':dmax_t,'maximum_watering_effect_difference_k_h':dmax_a,
                     'left_initial':l[date]['initial_after_spinup'], 'right_initial':r[date]['initial_after_spinup']})
    return {'common_dates':common, 'left_only_dates':sorted(l.keys()-r.keys()), 'right_only_dates':sorted(r.keys()-l.keys()),
            'compared_hourly_temperatures':count,'maximum_surface_difference_k':max_t if count else None,
            'maximum_watering_effect_difference_k_h':max_a if count else None,'worst_temperature':worst_t,'worst_effect':worst_a,
            'days':days}


def main():
    conv_path = Path('results/raw/convergence.json.gz')
    init_path = Path('results/raw/initialization.json.gz')
    c = load_weather(conv_path); i = load_weather(init_path)
    tolerance = c['config']['experiment']['temperature_convergence_tolerance_k']
    output = {'source_sha256':{str(p):digest(p) for p in (conv_path,init_path)},
              'config_sha256':c['source_hashes']['config'], 'checker_sha256':digest(__file__),
              'numerical_tolerance_k':tolerance, 'initial_state_tolerance_k':0.05,
              'initial_watering_effect_tolerance_k_h':1.2, 'convergence':{}, 'initialization':{}}
    steps = c['config']['experiment']['convergence_steps_s']
    for a,b in zip(steps,steps[1:]):
        left, right = f'step_{a}', f'step_{b}'
        check = compare(c['variants'][left],c['variants'][right])
        check['pass'] = bool(check['compared_hourly_temperatures'] and check['maximum_surface_difference_k'] <= tolerance
                             and not check['left_only_dates'] and not check['right_only_dates'])
        output['convergence'][f'{left}_to_{right}'] = check
    for name, days in i['variants'].items():
        if name == 'baseline': continue
        check = compare(i['variants']['baseline'],days)
        check['initial_state_independence_demonstrated'] = bool(check['compared_hourly_temperatures'] and
            check['maximum_surface_difference_k'] <= .05 and check['maximum_watering_effect_difference_k_h'] <= 1.2)
        output['initialization'][name] = check
    output['numerical_gate_pass'] = all(v['pass'] for v in output['convergence'].values())
    output['initialization_interpretation'] = ('conditional_on_initialization; no initialization-robust optimal-dose claim'
        if not all(v['initial_state_independence_demonstrated'] for v in output['initialization'].values()) else
        'coarse effects satisfy initial-state diagnostic; refined minimum-dose robustness not established')
    Path('results/gates.json').write_text(json.dumps(output,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    print(json.dumps({k:output[k] for k in ('numerical_gate_pass','initialization_interpretation')}))
    for group in ('convergence','initialization'):
        for name,x in output[group].items():
            print(name,len(x['common_dates']),x['maximum_surface_difference_k'],x['maximum_watering_effect_difference_k_h'])
    if not output['numerical_gate_pass']:
        raise SystemExit('Numerical gate failed: resolve before final stages')

if __name__ == '__main__':
    main()
