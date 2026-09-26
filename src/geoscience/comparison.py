"""Research batch entry point. DO NOT invoke with approval before user consent.

Preflight can import and inspect this module without running any integration.
"""
from __future__ import annotations
import argparse
import copy
import json
from datetime import datetime
from pathlib import Path
from .model import OutsideModelDomain
from .preflight import digest, load_weather, variants, prepare_day, treatment
from .simulate import run
from .weather import TIME_FORMAT


def metrics(result):
    """Hourly endpoint diagnostics; extrema/duration are hourly approximations."""
    initial = result['input']['initial_state']['surface_c']
    temps = [initial] + [r['state']['surface_c'] for r in result['interval_results']]
    air = [r['forcing']['air_c'] for r in result['input']['intervals']]
    excess = sum((max(a-t,0)+max(b-t,0))/2 for a,b,t in zip(temps,temps[1:],air))
    reflected=sum(r['duration_s']*r['forcing']['shortwave']*result['input']['parameters']['albedo'] for r in result['input']['intervals'])
    return {'reflected_shortwave_j_m2':reflected, 'hourly_surface_max_c':max(temps), 'hourly_surface_mean_c':sum((a+b)/2 for a,b in zip(temps,temps[1:]))/24,
            'air_excess_degree_hours':excess,
            'budget':result['total_budget'], 'final_state':result['interval_results'][-1]['state'],
            'water_residual_kg_m2':result['whole_run_water_residual_kg_m2'],
            'energy_residual_j_m2':result['whole_run_energy_residual_j_m2']}


def run_day(config, bundle, start, fractions, *, approved=False, integrator=run):
    if not approved:
        raise PermissionError('Additional user approval is required before all research integrations, including spin-up')
    spinup, evaluation = prepare_day(bundle, start, config)
    warm = integrator(spinup, authorize_research=True)
    check_budget(warm, config)
    initial = warm['interval_results'][-1]['state']
    results = []
    for albedo in config['experiment']['albedos']:
        for fraction in fractions:
            job = treatment(config,evaluation,initial,albedo,fraction)
            result = integrator(job,authorize_research=True)
            check_budget(result,config)
            results.append({'treatment':job['treatment'], 'metrics':metrics(result),
                            'hourly_states':[initial]+[r['state'] for r in result['interval_results']]})
    return {'start_kst':start.strftime(TIME_FORMAT),'initial_after_spinup':initial,'results':results}


def check_budget(result, config):
    ex=config['experiment']
    if abs(result['whole_run_water_residual_kg_m2']) > ex['water_residual_tolerance_kg_m2']:
        raise ArithmeticError('water conservation tolerance exceeded')
    if abs(result['whole_run_energy_residual_j_m2']) > ex['energy_residual_tolerance_j_m2']:
        raise ArithmeticError('energy conservation tolerance exceeded')


def choose_water(pairs, thresholds):
    """Paired common-day means only; no best dose when maximum benefit <= 0."""
    if not pairs:
        return {'status':'no_common_days'}
    if len({len(v) for v in pairs.values()}) != 1 or any(not v for v in pairs.values()):
        raise ValueError('all doses require identical nonempty paired cohorts')
    doses = sorted(pairs)
    means = {dose:sum(pairs[dose])/len(pairs[dose]) for dose in doses}
    best = max(means.values())
    return {'mean_effect_degree_hours':means, 'max_effect':best,
            'minimum_water_at_threshold':{str(q):(min(d for d in doses if means[d]>=q*best) if best>0 else None) for q in thresholds},
            'maximum_effect_minimum_tied_dose':(min(d for d in doses if means[d]==best) if best>0 else None),
            'limitation':'Optimum only within supplied dose grid and assumed properties, not a field recommendation.'}


def summarize(output, config, catalog):
    """Separate season/rain cohorts, pair all treatments; never optimize albedo."""
    groups={}
    metadata={d['start_kst']:d for d in catalog}
    common=set(output['common_dates_across_variants'])
    for name,days in output['variants'].items():
        by_group={}
        for day in days:
            if day['start_kst'] not in common:continue
            month=int(day['start_kst'][5:7]);meta=metadata[day['start_kst']]
            group=('summer' if month in (7,8) else 'winter')+'_'+meta['cohort']
            by_group.setdefault(group,[]).append(day)
        groups[name]={}
        for group,paired_days in by_group.items():
            summaries={}
            for albedo in config['experiment']['albedos']:
                effects={}; radiation=[]
                for day in paired_days:
                    arms=[x for x in day['results'] if x['treatment']['albedo']==albedo]
                    control=next(x for x in arms if x['treatment']['storage_fraction']==0)
                    radiation.append(control['metrics']['reflected_shortwave_j_m2'])
                    for arm in arms:
                        dose=arm['treatment']['delivered_water_kg_m2']
                        effects.setdefault(dose,[]).append(control['metrics']['air_excess_degree_hours']-arm['metrics']['air_excess_degree_hours'])
                summaries[str(albedo)]={'dose_choice':choose_water(effects,config['experiment']['effect_thresholds']),
                                       'mean_reflected_shortwave_j_m2':sum(radiation)/len(radiation)}
            groups[name][group]={'dates':[d['start_kst'] for d in paired_days],'by_albedo':summaries}
    return {'cohorts':groups,'albedo_winner':None,
            'pedestrian_limit':'Upward shortwave flux is not absorbed human radiation, MRT, UTCI, glare or a safety threshold.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=Path('config/research-inputs.json'))
    parser.add_argument('--prepared',type=Path,default=Path('data/preflight'))
    parser.add_argument('--stage',choices=['coarse','sensitivity','refinement','convergence'],default='coarse')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--approve-final-comparison',action='store_true')
    args=parser.parse_args()
    if not args.approve_final_comparison:
        parser.error('No research simulation authorized. Obtain explicit user approval first.')
    if args.output.exists():
        parser.error('refusing to overwrite an existing output')
    cfg=json.loads(args.config.read_text())
    if any(v=='pending_user' for v in cfg['decisions'].values()):
        parser.error('unresolved research decisions')
    manifest=json.loads((args.prepared/'manifest.json').read_text())
    if digest(args.config)!=manifest['source_hashes']['config']:
        parser.error('config changed since preflight; regenerate preparation')
    for path, sha in manifest['code_hashes'].items():
        if digest(path)!=sha:
            parser.error('code changed since preflight; regenerate preparation')
    weather_path=args.prepared/'incheon2025-weather.json.gz'
    if digest(weather_path)!=manifest['weather_bundle_sha256']:
        parser.error('weather bundle changed since preflight')
    bundle=load_weather(weather_path)
    all_variants=variants(cfg)
    selected=all_variants if args.stage=='sensitivity' else {'baseline':cfg}
    if args.stage=='convergence':
        selected={}
        for step in cfg['experiment']['convergence_steps_s']:
            v=copy.deepcopy(cfg);v['experiment']['max_step_s']=step;selected[str(step)]=v
    fractions=(cfg['experiment']['refinement_fractions'] if args.stage=='refinement'
               else cfg['experiment']['irrigation_storage_fractions'])
    output={'stage':args.stage,'config':cfg,'manifest':manifest,'variants':{},'exclusions':{},
            'warning':'Proxy sensitivity study; no experimental WCC validation. Apply common-day intersections before claims.'}
    for name,v in selected.items():
        ready_name=name if args.stage=='sensitivity' else 'baseline'
        output['variants'][name]=[];output['exclusions'][name]=[]
        for day in manifest['catalog']:
            if not day['variant_readiness'][ready_name]['ready']:
                output['exclusions'][name].append({'date':day['start_kst'],'reason':'preflight_not_ready'});continue
            try:
                result=run_day(v,bundle,datetime.strptime(day['start_kst'],TIME_FORMAT),fractions,approved=True)
                output['variants'][name].append(result)
            except OutsideModelDomain as error:
                # Whole date is excluded for all treatments, never only a wet arm.
                output['exclusions'][name].append({'date':day['start_kst'],'reason':str(error)})
    output['common_dates_across_variants']=sorted(set.intersection(*[
        {d['start_kst'] for d in days} for days in output['variants'].values()]))
    output['paired_summary']=summarize(output,cfg,manifest['catalog'])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as f:
        json.dump(output,f,ensure_ascii=False,indent=2,allow_nan=False)
    print('Completed approved stage; inspect common-day and convergence checks before interpreting.')

if __name__=='__main__':
    main()
