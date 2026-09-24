"""Measured live-twin validation run. Produces the table in docs/LIVE_SIMULATION.md section 4."""
import sqlite3, time, statistics as S
from pathlib import Path
import pandas as pd
from offline.config_schema import load_plant_config
from offline.benchmarks.simrun import with_horizon
from offline.live_twin.engine import LiveTwinEngine

cfg = with_horizon(load_plant_config('configs/plant_a.yaml'), 12)
thr = sqlite3.connect('artifacts/pulsetwin.db').execute(
    "SELECT value FROM metrics WHERE plant_id='plant_a' AND key='budget_threshold'").fetchone()[0]
twin = LiveTwinEngine(cfg, seed=42, artifacts=Path('artifacts'), budget_threshold=thr)
t0 = time.time(); twin.start()
print(f'warmup {time.time()-t0:.0f}s, sim {twin.now/3600:.1f}h', flush=True)

rows = []
for block in range(12):
    ev = twin.advance(6 * 3600)
    sc = [e for e in ev if e['event_type'] in ('scored', 'alert')]
    insp = [e for e in ev if e['event_type'] == 'inspection_outcome']
    dwell = [u for u in twin.units.values() if u.risk is not None]
    if sc:
        risks = [e['calibrated_risk'] for e in sc]
        rows.append(dict(
            sim_h=round(twin.now/3600, 1), scored=len(sc),
            alerts=sum(e['alert'] for e in sc),
            risk_p50=round(S.median(risks), 4), risk_max=round(max(risks), 4),
            unique=len(set(round(r, 6) for r in risks)),
            defects=sum(e['actual_defect'] for e in insp), inspected=len(insp),
        ))
        print(rows[-1], flush=True)

L = pd.DataFrame([dict(twin._flow_features(u), risk=u.risk)
                  for u in twin.units.values() if u.risk is not None])
print()
print('paint_buffer_dwell_s  p50=%.1f p95=%.1f max=%.1f' % (
    L['paint_buffer_dwell_s'].quantile(.5), L['paint_buffer_dwell_s'].quantile(.95),
    L['paint_buffer_dwell_s'].max()))
print('risk                  p50=%.4f p95=%.4f max=%.4f' % (
    L['risk'].quantile(.5), L['risk'].quantile(.95), L['risk'].max()))
s = twin.snapshot()
print('TOTALS scored=%d alerts=%d completed=%d defects=%d rate=%.3f%% uph=%.1f' % (
    s['units_scored'], s['alerts_opened'], s['units_completed'], s['defects_true'],
    100*s['observed_defect_rate'], s['throughput_uph']))
print('unique risks overall:', L['risk'].round(6).nunique(), 'of', len(L))
