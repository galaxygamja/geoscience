#!/usr/bin/env python3
"""Build portable, audited weather and experiment manifest; NEVER run a solver."""
import argparse
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.geoscience.preflight import audit, digest, material_job
from src.geoscience.preprocess import read_file, transform
from src.geoscience.weather import prepare, day_catalog


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--annual', type=Path, required=True)
    p.add_argument('--config', type=Path, default=Path('config/research-inputs.json'))
    p.add_argument('--output-dir', type=Path, default=Path('data/preflight'))
    args = p.parse_args()
    config = json.loads(args.config.read_text())
    daily_path = Path('data/kma_daily_precipitation_2025.json')
    daily = json.loads(daily_path.read_text())
    source_manifest = json.loads(Path('data/source_manifest.json').read_text())
    expected = source_manifest['files'][0]['sha256']
    if digest(args.annual) != expected:
        raise ValueError('annual file differs from reviewed original; review before preparing')
    raw, metadata = read_file(args.annual)
    rows = transform(raw)
    times = [r['time_kst'] for r in rows]
    if len(times) != len(set(times)):
        raise ValueError('duplicate input station timestamps')
    hourly = prepare(rows, wind_factor=config['weather']['wind_factor'],
                     deep_boundary='observed_30cm', daily=daily['days'])
    bundle = {'source':metadata, 'station':'112', 'timezone':'Asia/Seoul', 'daily_source':daily,
              'assumptions':{'wind_factor':config['weather']['wind_factor'], 'deep_boundary':'observed_30cm',
                             'reference':'docs/weather-integration.md'},
              'warning':'Meteorological observations plus labelled estimates, not a pavement validation dataset.',
              'day_catalog':day_catalog(hourly), 'hourly':hourly}
    report = audit(config, bundle)
    report['source_hashes'] = {'annual':digest(args.annual), 'daily':digest(daily_path), 'config':digest(args.config)}
    report['code_hashes'] = {str(p):digest(p) for p in sorted(Path('src/geoscience').glob('*.py'))}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    weather_path = args.output_dir / 'incheon2025-weather.json.gz'
    weather_bytes = json.dumps(bundle,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
    weather_path.write_bytes(gzip.compress(weather_bytes,mtime=0))
    report['weather_bundle_sha256'] = digest(weather_path)
    report['research_integrations_executed'] = 0
    for name, value in [('manifest.json',report), ('baseline-material.json',material_job(config))]:
        (args.output_dir/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    print(json.dumps({'status':report['status'],'counts':report['counts_by_month'],
                      'variants':len(report['variants']), 'research_integrations_executed':0},ensure_ascii=False,indent=2))

if __name__ == '__main__':
    main()
