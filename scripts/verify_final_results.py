#!/usr/bin/env python3
"""Artifact consistency proof using archived states and budgets, no integration."""
import json
import math
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.geoscience.preflight import load_weather,digest,material_job
from src.geoscience.model import State,energy
from src.geoscience.simulate import components


def main():
    root=Path('results')
    counts={}; max_mass=max_heat=max_hourly_sum=0.0
    baseline={}
    for stage in ('convergence','initialization','coarse','sensitivity','refinement'):
        raw=load_weather(root/'raw'/f'{stage}.json.gz')
        for path,sha in raw['code_hashes'].items():
            assert digest(path)==sha, ('source hash',path)
        assert digest('config/research-inputs.json')==raw['source_hashes']['config']
        assert digest('data/preflight/incheon2025-weather.json.gz')==raw['weather_bundle_sha256']
        count=0
        for variant,days in raw['variants'].items():
            cfg=raw['variant_configs'][variant];p=components(material_job(cfg))[0]
            seen=set()
            expected={(a,f) for a in cfg['experiment']['albedos'] for f in cfg['experiment']['refinement_fractions' if stage=='refinement' else 'irrigation_storage_fractions']}
            for day in days:
                date=day['start_kst'];assert date not in seen;seen.add(date)
                assert {(r['treatment']['albedo'],r['treatment']['storage_fraction']) for r in day['results']}==expected
                assert len(day['results'])==len(expected)
                for r in day['results']:
                    count+=1;b=r['metrics']['budget'];states=r['hourly_states'];initial=State(**states[0]);final=State(**states[-1])
                    assert states[0]==day['initial_after_spinup'];assert len(states)==25 and len(r['hourly_budgets'])==24
                    mass=(final.surface_water+final.base_water-initial.surface_water-initial.base_water
                          -(b['rain_kg_m2']+b['irrigation_kg_m2']-b['evaporated_kg_m2']-b['drained_kg_m2']-b['runoff_kg_m2']))
                    heat=(energy(final,p)-energy(initial,p)-(b['absorbed_shortwave_j_m2']+b['net_longwave_j_m2']
                          -b['sensible_out_j_m2']-b['latent_out_j_m2']-b['ground_out_j_m2']+b['water_enthalpy_net_j_m2']))
                    assert abs(mass)<=1e-6 and abs(heat)<=.1,(stage,variant,date,mass,heat)
                    assert math.isclose(b['latent_out_j_m2'],p.latent_heat*b['evaporated_kg_m2'],abs_tol=1e-6)
                    assert math.isclose(b['irrigation_kg_m2'],r['treatment']['delivered_water_kg_m2'],abs_tol=1e-8)
                    for field,value in b.items():
                        delta=abs(sum(h[field] for h in r['hourly_budgets'])-value)
                        max_hourly_sum=max(max_hourly_sum,delta)
                        assert delta<=1e-6,(stage,variant,date,field,delta)
                    max_mass=max(max_mass,abs(mass));max_heat=max(max_heat,abs(heat))
                    key=(date,r['treatment']['albedo'],r['treatment']['storage_fraction'])
                    if variant=='baseline' and stage=='coarse':baseline[key]=r
                    if variant=='baseline' and stage in ('sensitivity','refinement') and key in baseline:
                        assert r==baseline[key],('reused result mismatch',stage,key)
            excluded={x['date'] for x in raw['exclusions'][variant]}
            assert len(excluded)==len(raw['exclusions'][variant])
            assert not (excluded & seen)
            assert len(excluded | seen)==121
        counts[stage]=count
    summary=json.loads((root/'summary.json').read_text())
    for path,sha in summary['raw_sha256'].items():assert digest(path)==sha
    result={'status':'pass','treatment_records_verified':counts,'max_recomputed_water_residual_kg_m2':max_mass,
            'max_recomputed_energy_residual_j_m2':max_heat,'max_hourly_budget_sum_difference':max_hourly_sum,
            'checks':['source hashes','complete date accounting','treatment grids','common cloned initial state',
                      'independently recomputed mass and sensible-enthalpy balances','latent-water identity','irrigation amount',
                      'hourly budgets sum to totals','reused baseline records identical across stages','summary raw hashes'],
            'verifier_sha256':digest(__file__),'limitation':'Artifact consistency and conservation, not field validation.'}
    (root/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
