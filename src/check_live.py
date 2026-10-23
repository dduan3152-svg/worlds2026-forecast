"""Validate the mutable forecast summary separately from the frozen release manifest."""
from pathlib import Path
import json
import math
from live_forecast import ROOT, history_chain, export_results, STAGES, date
from check_repository import check_privacy


def main(root=ROOT):
    chain=history_chain(root)
    for i,snapshot in enumerate(chain):
        rows=snapshot['rows']
        if len(rows)!=16 or len({r['team'] for r in rows})!=16:raise ValueError('Expected 16 distinct Swiss teams')
        for key,total in zip(STAGES,[8,4,2,1]):
            if any(not math.isfinite(r[key]) or not 0<=r[key]<=1 for r in rows):raise ValueError('Invalid probability')
            if abs(sum(r[key] for r in rows)-total)>1e-9:raise ValueError('Invalid stage seat totals')
        if any(not r['qualified']>=r['semifinalist']>=r['finalist']>=r['champion'] for r in rows):raise ValueError('Invalid progression')
        if i:
            previous=chain[i-1]
            if date(snapshot['cutoff'])<date(previous['cutoff']):raise ValueError('Cutoff moved backwards')
            for stage in ['swiss','knockout']:
                old=previous['state'][stage]
                if snapshot['state'][stage][:len(old)]!=old:raise ValueError('Historical result changed')
    before=(root/'live/latest.json').read_bytes()
    export_results(root)
    if before!=(root/'live/latest.json').read_bytes():raise ValueError('Forecast summary was stale; regenerate and commit it')
    records=[{'path':str(p.relative_to(root))} for directory in ['live'] for p in (root/directory).rglob('*') if p.is_file() and p.suffix == '.json']
    check_privacy(records)
    print(f'Validated {len(chain)} immutable forecast versions and the latest result summary.')

if __name__=='__main__':main()
