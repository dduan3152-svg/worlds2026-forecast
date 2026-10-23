"""Versioned conditional forecasts. Network access is limited to one CC BY source."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import io
import json
import shutil
from pathlib import Path
import tempfile
import urllib.request

import numpy as np
from fetch_history import ALIASES
from simulator import simulate, to_jsonable, SWISS_START_UTC

ROOT = Path(__file__).resolve().parents[1]
SOURCE = 'https://chaincc.lol/data/chaincc-teams-2026.csv.gz'
SOURCE_PAGE = 'https://chaincc.lol/free/data/'
ROUND_COUNTS = {1: 8, 2: 8, 3: 8, 4: 6, 5: 3}
STAGES = ['qualified', 'semifinalist', 'finalist', 'champion']


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def canonical(value):
    return json.dumps(to_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        f.write(json.dumps(to_jsonable(value), ensure_ascii=False, indent=2, allow_nan=False).encode()+b'\n')
        name = f.name
    Path(name).replace(path)


def date(value):
    d = datetime.fromisoformat(value.replace('Z', '+00:00'))
    # ChainCC's unzoned dates follow the UTC convention of the frozen pipeline.
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)


def utc(d):
    return d.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def source_games(raw, teams, now):
    """Keep paired completed maps only; a changed schema or inconsistent map fails closed."""
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as f:
        payload = f.read(100_000_001)
    if len(payload) > 100_000_000:
        raise ValueError('数据解压大小超限')
    reader = csv.DictReader(io.StringIO(payload.decode('utf-8-sig')))
    required = {'game_id','date','league','team_name','opponent_team_name','side','result','gamelength','game_number'}
    if not required.issubset(reader.fieldnames or []):
        raise ValueError('数据字段已变化，需人工核查')
    grouped = defaultdict(list)
    latest = None
    for row in reader:
        start = date(row['date'])
        if start <= now and (latest is None or start > latest): latest = start
        if row['league'] not in {'WLDs','WORLDS','Worlds'} or start < date(SWISS_START_UTC) or start > now:
            continue
        a = ALIASES.get(row['team_name'])
        b = ALIASES.get(row['opponent_team_name'])
        if a not in teams or b not in teams:
            raise ValueError('世界赛出现未知队伍，需要核查名称映射')
        row = dict(row, a=a, b=b)
        grouped[row['game_id']].append(row)
    games = []
    for gid, rows in grouped.items():
        if len(rows) != 2:
            raise ValueError('逐局记录不完整或重复')
        x,y = rows
        if x['a'] != y['b'] or x['b'] != y['a'] or x['a'] == x['b'] or {x['side'],y['side']} != {'Blue','Red'}:
            raise ValueError('逐局双方身份不一致')
        if {x['result'],y['result']} != {'TRUE','FALSE'}:
            raise ValueError('逐局胜负不互补')
        if any(x[k] != y[k] for k in ['date','game_number','gamelength','league']):
            raise ValueError('逐局元数据不一致')
        length = float(x['gamelength'])
        if not 0 < length < 6*3600:
            raise ValueError('比赛时长无效')
        end = date(x['date']) + timedelta(seconds=length)
        if end > now: continue
        games.append({'id':gid,'a':x['a'],'b':x['b'],'winner':x['a'] if x['result']=='TRUE' else x['b'],
                      'number':int(x['game_number']),'start':utc(date(x['date'])),'end':utc(end)})
    games.sort(key=lambda x:(x['start'],x['number'],x['id']))
    return games, utc(latest) if latest else None


def infer_results(games, teams, opening, settings):
    """Derive rounds from completed series; never guess an unannounced bracket."""
    wins = Counter(); losses = Counter(); used = set(); current = {}
    swiss=[]; knockout=[]; pending=[]
    bracket=settings.get('quarterfinal_bracket')
    for g in games:
        a,b=g['a'],g['b']; key=tuple(sorted((a,b)))
        is_swiss = not all(wins[t]==3 or losses[t]==3 for t in teams)
        if is_swiss:
            if wins[a]==3 or losses[a]==3 or wins[b]==3 or losses[b]==3:
                raise ValueError('数据缺失或赛事阶段不一致')
            if (wins[a],losses[a]) != (wins[b],losses[b]):
                raise ValueError('赛果不满足同战绩配对，等待数据核查')
            rnd=wins[a]+losses[a]+1
            if any(sum(m['round']==r for m in swiss)!=ROUND_COUNTS[r] for r in range(1,rnd)):
                raise ValueError('前一轮赛果不完整，暂停推断后续轮次')
            if key in used:
                raise ValueError('瑞士轮重复交手，需要官方豁免人工核查')
            if rnd==1 and key not in {tuple(sorted(p)) for p in opening}:
                raise ValueError('首轮赛果与冻结对阵不一致')
            best_of=3 if wins[a]==2 or losses[a]==2 else 1
            stage='swiss'; identity=('swiss',rnd,key)
        else:
            if not bracket:
                pending.append('瑞士轮已结束；需在 live/settings.json 录入并注明来源的官方八强签位，才能接入淘汰赛赛果。')
                break
            stage='quarterfinal'; expected=bracket
            for previous,next_stage in [('quarterfinal','semifinal'),('semifinal','final')]:
                if stage != previous: continue
                records=[m for m in knockout if m['stage']==previous]
                if len(records) != len(expected): break
                winning=[next(m['winner'] for m in records if {m['a'],m['b']}==set(pair)) for pair in expected]
                expected=[winning[i:i+2] for i in range(0,len(winning),2)]
                stage=next_stage
            if any(m['stage']=='final' for m in knockout):
                raise ValueError('决赛结束后仍出现额外世界赛赛果')
            if key not in {tuple(sorted(p)) for p in expected} or any(m['stage']==stage and {m['a'],m['b']}==set(key) for m in knockout):
                raise ValueError('淘汰赛赛果与已确认签位不一致')
            best_of=5;identity=(stage,0,key)
        running=current.setdefault(identity,{'score':Counter(),'games':[]})
        if g['number'] != len(running['games'])+1:
            raise ValueError('系列赛局序缺失或重复，暂不采纳')
        running['games'].append(g['id']);running['score'][g['winner']]+=1
        if max(running['score'].values()) < best_of//2+1: continue
        winner=g['winner']; loser=b if winner==a else a
        record={'a':a,'b':b,'winner':winner,'best_of':best_of,'score_a':running['score'][a],
                'score_b':running['score'][b],'ended_at':g['end'],'game_ids':running['games']}
        del current[identity]
        if is_swiss:
            record['round']=rnd; swiss.append(record);wins[winner]+=1;losses[loser]+=1;used.add(key)
        else:
            record['stage']=stage;knockout.append(record)
    if current: pending.append(f'{len(current)} 场系列赛尚未收齐结束赛果。')
    accepted=[]
    for rnd in range(1,6):
        rows=[m for m in swiss if m['round']==rnd]
        if not rows: break
        if len(rows)==ROUND_COUNTS[rnd] or rnd==1 or str(rnd) in settings.get('announced_rounds',{}):
            accepted.extend(rows)
        else:
            pending.append(f'瑞士第 {rnd} 轮已有 {len(rows)}/{ROUND_COUNTS[rnd]} 场赛果；待全轮结束或录入完整官方对阵后重算。')
            break
    if len(accepted)==33 and not bracket:
        pending.append('瑞士轮已结束；等待录入附来源的官方八强签位。')
    return accepted, knockout if len(accepted)==33 else [], list(dict.fromkeys(pending))


def make_snapshot(root, state, source_hash, now, n):
    base=load(root/'outputs/simulation_base.json'); teams=base['teams']
    reader=csv.DictReader(io.StringIO((root/'outputs/matrix_bo1.csv').read_text()))
    label=reader.fieldnames[0]
    matrix={r[label]:r for r in reader}
    p=np.array([[float(matrix[a][b]) for b in teams] for a in teams])
    result=simulate(teams,[(x['a'],x['b']) for x in load(root/'data/known_state.json')['first_round']],p,n,20261023,
        known_matches=state['swiss'],announced_rounds={int(k):v for k,v in state['announced_rounds'].items()},
        quarterfinal_bracket=state['quarterfinal_bracket'],known_knockout=state['knockout'],
        as_of_utc=state['cutoff'],keep_examples=0)
    meta={t['code']:t for t in load(root/'data/teams.json')}
    rows=[]
    for i,t in enumerate(teams):
        w=sum(m['winner']==t for m in state['swiss']);l=sum(t in [m['a'],m['b']] and m['winner']!=t for m in state['swiss'])
        rows.append({'team':t,'name':meta[t]['name'],'region':meta[t]['region'],'wins':w,'losses':l,
                     **{k:float(result['probabilities'][k][i]) for k in STAGES}})
    snapshot={'schema':1,'id':digest(state)[:16],'cutoff':state['cutoff'],'created_at':utc(now),'model_cutoff':load(root/'config.json')['cutoff_utc'],
              'kind':'conditional_update','n':n,'rows':rows,'state':state,'source_sha256':source_hash,
              'previous':None,'checks':{k:v for k,v in to_jsonable(result['checks']).items() if k!='draw_cache'}}
    return snapshot


def bootstrap(root):
    history=root/'live/history'
    if list(history.glob('*.json')): return
    config=load(root/'config.json');meta={t['code']:t for t in load(root/'data/teams.json')}
    rows=[]
    for r in csv.DictReader(io.StringIO((root/'outputs/stage_probabilities.csv').read_text())):
        if r['team'] not in meta:continue
        t=r['team'];rows.append({'team':t,'name':meta[t]['name'],'region':meta[t]['region'],'wins':0,'losses':0,**{k:float(r[k]) for k in STAGES}})
    state={'swiss':[],'knockout':[],'announced_rounds':{},'quarterfinal_bracket':None,'cutoff':config['cutoff_utc']}
    snap={'schema':1,'id':digest(state)[:16],'cutoff':config['cutoff_utc'],'created_at':config['cutoff_utc'],
          'model_cutoff':config['cutoff_utc'],'kind':'frozen_baseline','n':config['simulation_count'],'rows':rows,'state':state,
          'previous':None,'source_sha256':None,'checks':{'frozen_validation':True}}
    snap['hash']=digest(snap)
    write(history/(snap['id']+'.json'),snap)


def history_chain(root):
    snapshots=[load(p) for p in (root/'live/history').glob('*.json')]
    by_hash={s['hash']:s for s in snapshots}
    if len(by_hash)!=len(snapshots): raise ValueError('重复历史版本')
    for s in snapshots:
        if digest({k:v for k,v in s.items() if k!='hash'})!=s['hash'] or s['id']!=digest(s['state'])[:16]:
            raise ValueError('历史版本校验失败')
    chain=[];previous=None
    while len(chain)<len(snapshots):
        following=[s for s in snapshots if s['previous']==previous]
        if len(following)!=1:raise ValueError('历史记录分叉或缺失')
        chain.append(following[0]);previous=following[0]['hash']
    return chain


def export_site(root):
    chain=history_chain(root)
    bootstrap_rows=list(csv.DictReader(io.StringIO((root/'outputs/bootstrap_summary.csv').read_text())))
    reader=csv.DictReader(io.StringIO((root/'outputs/matrix_bo1.csv').read_text()))
    label=reader.fieldnames[0]
    matrix=list(reader)
    state=load(root/'data/known_state.json')
    payload={'schema':1,'history':chain,'status':load(root/'live/status.json') if (root/'live/status.json').exists() else {'state':'ready','message':'等待定时检查公开数据。'},
             'first_round':[{k:r[k] for k in ['a','b','start_bjt','start_time_is_approximate']} for r in state['first_round']],
             'intervals':{r['team']:[float(r['champion_q05']),float(r['champion_q95'])] for r in bootstrap_rows},
             'matrix':{r[label]:{k:float(v) for k,v in r.items() if k!=label} for r in matrix},
             'source':{'name':'ChainCC','url':SOURCE_PAGE,'license':'CC BY 4.0'}}
    write(root/'site/data.json',payload)
    shutil.copyfile(root/'docs/worlds2026_report.pdf',root/'site/report.pdf')
    if (root/'site/index.html').exists():
        from build_static_page import build
        build(root)


def update(root, raw, now):
    bootstrap(root); settings=load(root/'live/settings.json');previous=history_chain(root)[-1]
    teams=load(root/'outputs/simulation_base.json')['teams']
    first=[(r['a'],r['b']) for r in load(root/'data/known_state.json')['first_round']]
    games,latest=source_games(raw,teams,now)
    swiss,knockout,pending=infer_results(games,teams,first,settings)
    for key,new in [('swiss',swiss),('knockout',knockout)]:
        old=previous['state'][key]
        if new[:len(old)]!=old:raise ValueError('来源撤回或修正了已采用赛果；保留上版，需人工核查')
    draws=settings.get('announced_rounds',{})
    bracket=settings.get('quarterfinal_bracket')
    if draws or bracket:
        if not settings.get('draw_source_url','').startswith('https://') or not settings.get('draw_as_of'):
            raise ValueError('手动签位必须附 HTTPS 来源与公布时间')
        if date(settings['draw_as_of'])>now:raise ValueError('签位公布时间不能在未来')
    cutoff=max([date(load(root/'config.json')['cutoff_utc'])]+[date(m['ended_at']) for m in swiss+knockout]+([date(settings['draw_as_of'])] if draws or bracket else []))
    state={'swiss':swiss,'knockout':knockout,'announced_rounds':draws,'quarterfinal_bracket':bracket,'cutoff':utc(cutoff)}
    changed=digest(state)!=digest(previous['state'])
    if changed:
        n=int(settings.get('simulations',100000))
        if not 100<=n<=1000000:raise ValueError('模拟次数必须为 100 至 1000000')
        snapshot=make_snapshot(root,state,hashlib.sha256(raw).hexdigest(),now,n)
        snapshot['previous']=previous['hash'];snapshot['hash']=digest(snapshot)
        destination=root/'live/history'/(snapshot['id']+'.json')
        if destination.exists():raise ValueError('拒绝覆盖已有预测版本')
        write(destination,snapshot)
    stale=latest is None or now-date(latest)>timedelta(hours=36)
    message='已接入新赛果并重算。' if changed else '未发现可采纳的新赛果，保留原预测。'
    if now<date(SWISS_START_UTC):
        message='瑞士轮尚未开始，当前展示冻结赛前预测；任务将继续定时检查公开赛果。'
        stale=False
    elif pending:message=' '.join(pending)
    elif stale:message+=' 数据源超过 36 小时未出现新比赛记录，更新可能延迟。'
    write(root/'live/status.json',{'state':'waiting' if pending or stale else 'ok','message':message,'checked_at':utc(now),
        'source_latest_at':latest,'source_sha256':hashlib.sha256(raw).hexdigest(),'observed_worlds_maps':len(games),
        'accepted_series':len(swiss)+len(knockout),'pending':pending,'stale':stale})
    export_site(root)
    return changed


def fetch_source():
    # Fixed public, licensed download URL: no credentials, cookies or account API.
    req=urllib.request.Request(SOURCE,headers={'User-Agent':'WorldsForecast/1.0 (CC-BY-data-reader)'})
    with urllib.request.urlopen(req,timeout=60) as response:
        data=response.read(20_000_001)
    if len(data)>20_000_000:raise ValueError('下载大小超限')
    return data


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--fetch',action='store_true',help='Fetch the single public CC BY CSV source')
    parser.add_argument('--input',type=Path,help='Use a local gzip snapshot instead of the network')
    parser.add_argument('--export-only',action='store_true')
    args=parser.parse_args();root=args.root.resolve();bootstrap(root)
    if args.export_only:
        export_site(root);return
    if args.fetch==bool(args.input):parser.error('Choose exactly one of --fetch or --input')
    try:
        changed=update(root,fetch_source() if args.fetch else args.input.read_bytes(),datetime.now(timezone.utc))
        print('New forecast saved.' if changed else 'No new forecast; prior values preserved.')
    except Exception as exc:
        # Do not expose machine paths, response bodies or credential material in the website.
        message=str(exc) if isinstance(exc,ValueError) else '数据源暂不可用或更新失败；保留最后一次有效预测。'
        previous=load(root/'live/status.json') if (root/'live/status.json').exists() else {}
        write(root/'live/status.json',{**previous,'state':'error','message':message,'checked_at':utc(datetime.now(timezone.utc))})
        export_site(root)
        print('Update failed safely; inspect source data and settings.');raise SystemExit(1)

if __name__=='__main__':main()
