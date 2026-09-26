#!/usr/bin/env python3
"""Static scientific figures from reviewed CSV tables; no simulation calls."""
import csv
import os
from pathlib import Path
os.environ.setdefault('MPLCONFIGDIR',str(Path('tmp/matplotlib').resolve()))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

ROOT=Path('results'); FIG=ROOT/'figures'
COLORS={.1:'#B85C20',.3:'#257598',.5:'#687A39'}
MARKERS={.1:'o',.3:'s',.5:'^'}
GROUPS={'summer_zero_recorded_amount':'Summer: zero recorded rain','summer_reported_rain':'Summer: reported rain'}
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'axes.spines.top':False,'axes.spines.right':False,
                     'axes.labelcolor':'#25313A','text.color':'#25313A','axes.titleweight':'bold',
                     'grid.color':'#DCE1E5','grid.linewidth':.6,'savefig.facecolor':'white'})


def read(name):
    with (ROOT/name).open() as f: rows=list(csv.DictReader(f))
    for row in rows:
        for key,value in row.items():
            if key not in ('scope','variant','cohort','status'):
                try:row[key]=float(value)
                except (ValueError,TypeError):pass
    return rows


def save(fig,name,note):
    fig.text(.06,.015,note,fontsize=9,color='#52616C')
    fig.savefig(FIG/(name+'.png'),dpi=180,bbox_inches='tight')
    fig.savefig(FIG/(name+'.svg'),bbox_inches='tight')
    plt.close(fig)


def main():
    FIG.mkdir(parents=True,exist_ok=True)
    baseline=[r for r in read('baseline_summary.csv') if r['scope']=='baseline_all']
    doses=[r for r in read('dose_response.csv') if r['scope']=='baseline_all']
    sensitivity=read('sensitivity_summary.csv')
    fig,axes=plt.subplots(1,2,figsize=(12.4,4.8),layout='constrained')
    images=[]
    for ax,(cohort,title) in zip(axes,GROUPS.items()):
        rows=[r for r in baseline if r['cohort']==cohort]
        matrix=np.array([[next(r['mean_daily_max_surface_c'] for r in rows if r['albedo']==a and r['storage_fraction']==f) for f in (0,.5,1)] for a in (.1,.3,.5)])
        images.append(ax.imshow(matrix,cmap='YlOrRd',vmin=34,vmax=55,aspect='auto'))
        for i in range(3):
            for j in range(3):
                ax.text(j,i,f'{matrix[i,j]:.2f}',ha='center',va='center',color='white' if matrix[i,j]>49 else '#222',fontsize=13)
        ax.set(xticks=[0,1,2],xticklabels=['0','2.975','5.950'],yticks=[0,1,2],yticklabels=['0.10','0.30','0.50'],xlabel='Delivered water (L/m²)',ylabel='Albedo',title=f'{title} (n={int(rows[0]["n_days"])})')
    fig.colorbar(images[0],ax=axes,label='Mean of hourly daily maxima (°C)',shrink=.82)
    fig.suptitle('Baseline 24-hour surface temperature comparison',fontsize=16)
    fig.set_constrained_layout_pads(h_pad=.30)
    save(fig,'baseline-temperature','Incheon 2025 eligible Jul–Aug dates · WCC-family/RCA proxy · common initial state, 72-hour prehistory')

    fig,axes=plt.subplots(1,2,figsize=(12.4,5.1),layout='constrained',sharey=True)
    for ax,(cohort,title) in zip(axes,GROUPS.items()):
        for a in (.1,.3,.5):
            rows=sorted([r for r in doses if r['cohort']==cohort and r['albedo']==a],key=lambda r:r['delivered_water_l_m2'])
            x=[r['delivered_water_l_m2'] for r in rows];y=[r['mean_watering_effect_k_h'] for r in rows]
            ax.plot(x,y,color=COLORS[a],marker=MARKERS[a],markevery=4,markersize=5,label=f'Albedo {a:.2f}')
        ax.set(title=f'{title} (n={int(rows[0]["n_days"])})',xlabel='Delivered water (L/m²)',xlim=(0,6),ylim=(0,None))
        ax.grid(axis='y');ax.legend(frameon=False,loc='upper left')
    axes[0].set_ylabel('Mean reduction of positive Ts − Ta integral (K·h)')
    fig.suptitle('Watering benefit relative to no irrigation at the same albedo',fontsize=15)
    fig.set_constrained_layout_pads(h_pad=.32)
    save(fig,'water-dose-response','21-point grid · no field-optimum claim · 95%/90% thresholds refer only to the largest effect inside this grid')

    fig,axes=plt.subplots(2,3,figsize=(13,8.3),layout='constrained',sharey='col')
    specs=[('mean_daily_max_surface_c','Daily maximum surface T (°C)'),('mean_evaporated_kg_m2','Evaporation over 24 h (kg/m²)'),('mean_reflected_shortwave_mj_m2','Upward reflected shortwave (MJ/m²)')]
    for row,(cohort,title) in enumerate(GROUPS.items()):
        for col,(metric,label) in enumerate(specs):
            ax=axes[row,col]
            for f,style,marker in ((0,'--','o'),(1,'-','s')):
                data=sorted([r for r in baseline if r['cohort']==cohort and r['storage_fraction']==f],key=lambda r:r['albedo'])
                ax.plot([r['albedo'] for r in data],[r[metric] for r in data],color='#257598' if f else '#68737B',ls=style,marker=marker,label='5.95 L/m²' if f else 'No irrigation')
            ax.set(xlabel='Albedo',ylabel=label,xticks=[.1,.3,.5]);ax.grid(axis='y')
            if col:ax.set_ylim(bottom=0)
            ax.set_title(title+f' (n={int(data[0]["n_days"])})',fontsize=11)
            if col==0:ax.legend(frameon=False)
    fig.suptitle('Lower surface temperature, lower evaporation, more reflected sunlight',fontsize=15)
    fig.set_constrained_layout_pads(h_pad=.33)
    save(fig,'albedo-evaporation-reflection','Model results, not measurements · reflected SW curves overlap by definition · reflected SW is not pedestrian absorbed heat or comfort')

    fig,axes=plt.subplots(1,2,figsize=(13,6.8),layout='constrained',sharex=True)
    for ax,(cohort,title) in zip(axes,GROUPS.items()):
        data=[r for r in sensitivity if r['cohort']==cohort and r['albedo']==.3 and r['storage_fraction']==1]
        ref=next(r['mean_watering_effect_k_h'] for r in data if r['variant']=='baseline')
        ranked=sorted([r for r in data if r['variant']!='baseline'],key=lambda r:abs(r['mean_watering_effect_k_h']-ref),reverse=True)[:10]
        ranked.sort(key=lambda r:r['mean_watering_effect_k_h']-ref)
        values=[r['mean_watering_effect_k_h']-ref for r in ranked]
        ax.barh([r['variant'] for r in ranked],values,color=['#B85C20' if x<0 else '#257598' for x in values])
        ax.axvline(0,color='#25313A',lw=.8);ax.grid(axis='x');ax.set_axisbelow(True)
        ax.set(title=f'{title} (n={int(data[0]["n_days"])})\nBaseline benefit: {ref:.2f} K·h',xlabel='Change in watering benefit vs baseline (K·h)')
    fig.suptitle('Largest one-at-a-time sensitivities: albedo 0.30, full storage-fraction dose',fontsize=14)
    fig.set_constrained_layout_pads(h_pad=.35)
    save(fig,'sensitivity','Common dates across all 30 scenarios · top 10 absolute changes per cohort · scenario spread, not confidence intervals\nStorage scenarios also change delivered water; identifiers and all values are in the configuration and CSV tables.')
    print('Wrote four PNG/SVG figure pairs to',FIG)

if __name__=='__main__':main()
