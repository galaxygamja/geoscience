#!/usr/bin/env python3
"""Rebuild paired, cohort-specific research tables from archived solver outputs."""
from __future__ import annotations
import csv
import json
import statistics
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.geoscience.preflight import digest, load_weather
from src.geoscience.comparison import choose_water

ROOT = Path('results')
GROUPS = ('winter_zero_recorded_amount','winter_reported_rain','summer_zero_recorded_amount','summer_reported_rain')


def group(date, catalog):
    season = 'summer' if int(date[5:7]) in (7,8) else 'winter'
    return season + '_' + catalog[date]['cohort']


def mean(values):
    return statistics.fmean(values)


def write_csv(name, rows):
    if not rows:
        raise ValueError('no rows for ' + name)
    with (ROOT / name).open('w',newline='') as f:
        writer = csv.DictWriter(f,fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)


def summarize_days(days, cohort, scope, variant):
    if not days: return []
    keyed = [{(x['treatment']['albedo'],x['treatment']['storage_fraction']):x for x in d['results']} for d in days]
    keys = set(keyed[0])
    if any(set(x)!=keys for x in keyed): raise ValueError('treatment grids differ')
    output=[]
    for a,f in sorted(keys):
        arms = [x[a,f] for x in keyed]
        controls = [x[a,0] for x in keyed]
        effects = [c['metrics']['air_excess_degree_hours']-x['metrics']['air_excess_degree_hours'] for x,c in zip(arms,controls)]
        row = {'scope':scope,'variant':variant,'cohort':cohort,'n_days':len(days),'albedo':a,'storage_fraction':f,
               'delivered_water_l_m2':arms[0]['treatment']['delivered_water_kg_m2'],
               'mean_daily_max_surface_c':mean([x['metrics']['hourly_surface_max_c'] for x in arms]),
               'mean_surface_c':mean([x['metrics']['hourly_surface_mean_c'] for x in arms]),
               'mean_air_excess_k_h':mean([x['metrics']['air_excess_degree_hours'] for x in arms]),
               'mean_watering_effect_k_h':mean(effects),'min_daily_watering_effect_k_h':min(effects),
               'max_daily_watering_effect_k_h':max(effects),
               'mean_daily_max_reduction_by_watering_k':mean([c['metrics']['hourly_surface_max_c']-x['metrics']['hourly_surface_max_c'] for x,c in zip(arms,controls)]),
               'mean_daily_mean_reduction_by_watering_k':mean([c['metrics']['hourly_surface_mean_c']-x['metrics']['hourly_surface_mean_c'] for x,c in zip(arms,controls)]),
               'mean_reflected_shortwave_mj_m2':mean([x['metrics']['reflected_shortwave_j_m2']/1e6 for x in arms]),
               'mean_initial_surface_water_kg_m2':mean([d['initial_after_spinup']['surface_water'] for d in days]),
               'mean_initial_base_water_kg_m2':mean([d['initial_after_spinup']['base_water'] for d in days]),
               'mean_final_surface_water_kg_m2':mean([x['metrics']['final_state']['surface_water'] for x in arms]),
               'mean_final_base_water_kg_m2':mean([x['metrics']['final_state']['base_water'] for x in arms]),
               'mean_extra_evaporation_vs_no_water_kg_m2':mean([x['metrics']['budget']['evaporated_kg_m2']-c['metrics']['budget']['evaporated_kg_m2'] for x,c in zip(arms,controls)]),
               'mean_interaction_k_h':mean([x[a,f]['metrics']['air_excess_degree_hours']-x[a,0]['metrics']['air_excess_degree_hours']
                      -x[.1,f]['metrics']['air_excess_degree_hours']+x[.1,0]['metrics']['air_excess_degree_hours'] for x in keyed])}
        for field in arms[0]['metrics']['budget']:
            if 'residual' not in field:
                row['mean_'+field] = mean([x['metrics']['budget'][field] for x in arms])
        output.append(row)
    return output


def main():
    stages = {k:load_weather(ROOT/'raw'/f'{k}.json.gz') for k in ('convergence','initialization','coarse','sensitivity','refinement')}
    manifest=json.loads(Path('data/preflight/manifest.json').read_text())
    catalog={x['start_kst']:x for x in manifest['catalog']}
    common = set(stages['sensitivity']['common_dates_across_variants'])
    coarse = stages['coarse']['variants']['baseline']
    refined = stages['refinement']['variants']['baseline']
    tables = {'baseline_summary.csv':[], 'dose_response.csv':[], 'sensitivity_summary.csv':[]}
    for label,days,destination,scope,variant in [
            ('baseline',coarse,'baseline_summary.csv','baseline_all','baseline'),
            ('baseline_common',[d for d in coarse if d['start_kst'] in common],'baseline_summary.csv','sensitivity_common','baseline'),
            ('refinement',refined,'dose_response.csv','baseline_all','baseline'),
            ('refinement_common',[d for d in refined if d['start_kst'] in common],'dose_response.csv','sensitivity_common','baseline')]:
        for cohort in GROUPS:
            selected=[d for d in days if group(d['start_kst'],catalog)==cohort]
            tables[destination] += summarize_days(selected,cohort,scope,variant)
    for variant,days in stages['sensitivity']['variants'].items():
        for cohort in GROUPS:
            selected=[d for d in days if d['start_kst'] in common and group(d['start_kst'],catalog)==cohort]
            tables['sensitivity_summary.csv'] += summarize_days(selected,cohort,'sensitivity_common',variant)
    for filename,rows in tables.items(): write_csv(filename,rows)
    counts=[]; dates={}; choices=[]
    for cohort in GROUPS:
        selected=[d for d in refined if group(d['start_kst'],catalog)==cohort]
        coarse_dates=[d['start_kst'] for d in coarse if group(d['start_kst'],catalog)==cohort]
        common_dates=sorted(d for d in common if group(d,catalog)==cohort)
        dates[cohort]={'baseline':coarse_dates,'sensitivity_common':common_dates,'refinement':[d['start_kst'] for d in selected]}
        counts.append({'cohort':cohort,'preflight_ready':sum(d['baseline_preflight_ready'] and group(date,catalog)==cohort for date,d in catalog.items()),
                       'coarse_complete':len(coarse_dates),'sensitivity_common':len(common_dates),'refinement_complete':len(selected)})
        for scope in ('baseline_all','sensitivity_common'):
            paired=[d for d in selected if scope=='baseline_all' or d['start_kst'] in common]
            for a in (.1,.3,.5):
                pairs={}
                for d in paired:
                    arms={x['treatment']['storage_fraction']:x for x in d['results'] if x['treatment']['albedo']==a}
                    for x in arms.values():
                        pairs.setdefault(x['treatment']['delivered_water_kg_m2'],[]).append(arms[0]['metrics']['air_excess_degree_hours']-x['metrics']['air_excess_degree_hours'])
                choice=choose_water(pairs,[.95,.9])
                row={'scope':scope,'cohort':cohort,'n_days':len(paired),'albedo':a,'max_effect_k_h':choice.get('max_effect'),
                     'minimum_95_l_m2':choice.get('minimum_water_at_threshold',{}).get('0.95'),
                     'minimum_90_l_m2':choice.get('minimum_water_at_threshold',{}).get('0.9'),
                     'dose_at_grid_maximum_l_m2':choice.get('maximum_effect_minimum_tied_dose'),
                     'status':'conditional_on_baseline_initialization' if paired else 'no_eligible_days'}
                choices.append(row)
    write_csv('cohort_counts.csv',counts); write_csv('water_choices.csv',choices)
    conservation={}
    for stage,data in stages.items():
        mass=[]; heat=[]; n=0
        for days in data['variants'].values():
            for d in days:
                mass.append(abs(d['spinup_metrics']['water_residual_kg_m2'])); heat.append(abs(d['spinup_metrics']['energy_residual_j_m2']))
                for x in d['results']:
                    n+=1; mass.append(abs(x['metrics']['water_residual_kg_m2'])); heat.append(abs(x['metrics']['energy_residual_j_m2']))
        conservation[stage]={'archived_treatment_results':n,'max_abs_water_residual_kg_m2':max(mass),'max_abs_energy_residual_j_m2':max(heat)}
    ranges=[]
    for cohort in GROUPS:
        for a in (.1,.3,.5):
            rows=[r for r in tables['sensitivity_summary.csv'] if r['cohort']==cohort and r['albedo']==a and r['storage_fraction']==1]
            if not rows: continue
            ranges.append({'cohort':cohort,'albedo':a,'n_days':rows[0]['n_days'],
                           'minimum_effect_k_h':min(r['mean_watering_effect_k_h'] for r in rows),
                           'maximum_effect_k_h':max(r['mean_watering_effect_k_h'] for r in rows),
                           'lowest_effect_variant':min(rows,key=lambda r:r['mean_watering_effect_k_h'])['variant'],
                           'highest_effect_variant':max(rows,key=lambda r:r['mean_watering_effect_k_h'])['variant'],
                           'warning':'OAT scenario envelope, not a probability interval; storage variants also alter absolute dose.'})
    write_csv('sensitivity_ranges.csv',ranges)
    output={'raw_sha256':{str(ROOT/'raw'/f'{k}.json.gz'):digest(ROOT/'raw'/f'{k}.json.gz') for k in stages},
            'analysis_sha256':digest(__file__),'counts':counts,'dates':dates,'water_choices':choices,
            'conservation':conservation,'sensitivity_ranges':ranges,
            'metrics_note':'Hourly endpoint extrema and trapezoidal positive Ts-Ta integral; arithmetic mean across paired dates. Dose thresholds relative to same-albedo no-irrigation control, within 0..5.95 L/m2 grid only.',
            'limitations':['initialization-dependent proxy study','one winter day is not winter season evidence',
                           'common alpha=0.30 prehistory, 24-hour intervention','upward reflected shortwave is not pedestrian dose or comfort',
                           'minimum dose not tested across all uncertain parameters; grid maximum may be at upper boundary']}
    (ROOT/'summary.json').write_text(json.dumps(output,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    print(json.dumps({'counts':counts,'choices':choices[:12],'conservation':conservation},ensure_ascii=False,indent=2))

if __name__=='__main__': main()
