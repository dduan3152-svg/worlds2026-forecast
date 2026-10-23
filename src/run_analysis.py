"""End-to-end frozen-data analysis. Run from any directory, Python 3.11+."""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
from time import perf_counter
from datetime import datetime,timezone

import numpy as np
import pandas as pd

from model import BTModel,fit_bt,choose_hyperparameters,rolling_evaluate,calibration_table,series_probability
from prepare_model_data import prepare
from simulator import simulate,to_jsonable

ROOT=Path(__file__).resolve().parents[1]


def save_json(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(to_jsonable(obj),ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def load_model(path):
    value=json.loads(path.read_text())
    return BTModel(**{k:(np.asarray(v) if k=='theta' else v)
                      for k,v in value.items() if k in BTModel.__dataclass_fields__})


def fit_phase(root,config,obs,regions,audit):
    start=perf_counter()
    print('Selecting fixed hyperparameters on May-July validation folds...',flush=True)
    params,tuning=choose_hyperparameters(obs,config['tuning_folds'],
        config['grid_half_life_days'],config['grid_ridge'],config['grid_temperature'])
    tuning.to_csv(root/'outputs/hyperparameter_search.csv',index=False)
    save_json(root/'outputs/chosen_parameters.json',params)
    print('Selected:',params,flush=True)
    pred,metrics=rolling_evaluate(obs,config['test_folds'],params)
    pred.to_csv(root/'outputs/backtest_predictions.csv',index=False)
    metrics.to_csv(root/'outputs/backtest_metrics.csv',index=False)
    calibration_table(pred,pred.p_bt,bins=5).to_csv(root/'outputs/calibration.csv',index=False)
    # A separate international slice makes the smaller external-validity sample visible.
    from model import probability_metrics
    sub=pred[pred.cross_region]
    submetrics=[]
    if len(sub):
        for name,col in [('BT','p_bt'),('BatchElo','p_elo'),('Half','p_half')]:
            submetrics.append({'slice':'cross_region_test','model':name,**probability_metrics(sub,sub[col])})
    save_json(root/'outputs/international_backtest.json',submetrics)
    model=fit_bt(obs,config['cutoff_utc'],**params)
    save_json(root/'outputs/fitted_model.json',model.as_dict())
    codes=[x['code'] for x in json.loads((root/'data/teams.json').read_text())]
    p=model.matrix(codes)
    if not np.allclose(p+p.T,1) or not np.allclose(np.diag(p),.5):
        raise AssertionError('Probability complementarity')
    for bo in [1,3,5]:
        pd.DataFrame(series_probability(p,bo),index=codes,columns=codes).rename_axis('row_team_beats_column').to_csv(root/f'outputs/matrix_bo{bo}.csv')
    rows=[]
    for t in codes:
        mask=(obs.team_a==t)|(obs.team_b==t)
        wins=np.where(obs.team_a==t,obs.wins_a,obs.wins_b)
        rows.append({'team':t,'strength_logit':model.strength(t),'observed_maps':int(obs.loc[mask,'map_count'].sum()),
                     'series':int(obs.loc[mask,'series_id'].nunique()),
                     'map_wins':int(wins[mask].sum()),'international_maps':int(obs.loc[mask&obs.cross_region,'map_count'].sum()),
                     'last_match_start':str(obs.loc[mask,'date'].max())})
    pd.DataFrame(rows).sort_values('strength_logit',ascending=False).to_csv(root/'outputs/team_strengths.csv',index=False)
    save_json(root/'outputs/fit_log.json',{'completed':True,'seconds':perf_counter()-start,'parameters':params,
       'tuning_end':'2026-08-01T00:00:00Z','test_metrics_not_used_for_parameter_selection':True,
       'frozen_monthly_folds_for_both_BT_and_Elo':True,'side_coefficient':model.side_beta,
       'side_causal_interpretation':False,'model':model.as_dict()})
    print(metrics[metrics.fold_start=='ALL_TEST'].to_string(index=False),flush=True)
    print('Final strengths:',pd.DataFrame(rows).sort_values('strength_logit',ascending=False)[['team','strength_logit']].to_string(index=False),flush=True)
    return model


def tournament_inputs(root,config):
    teams=json.loads((root/'data/teams.json').read_text())
    state=json.loads((root/'data/known_state.json').read_text())
    if pd.Timestamp(state['cutoff'])!=pd.Timestamp(config['cutoff_utc']):
        raise ValueError('Update BOTH known_state.cutoff and config.cutoff_utc explicitly.')
    codes=[x['code'] for x in teams]
    first=[(x['a'],x['b']) for x in state['first_round']]
    known=list(state.get('known_matches',[]))
    existing={(int(x['round']),frozenset((x['a'],x['b']))) for x in known}
    for x in state['first_round']:
        if x.get('completed') and (1,frozenset((x['a'],x['b']))) not in existing:
            known.append({'round':1,'a':x['a'],'b':x['b'],'winner':x['winner']})
    kwargs={'known_matches':known,'announced_rounds':state.get('announced_rounds',{}),
            'as_of_utc':config['cutoff_utc']}
    # Optional knockout conditioning is supported by the simulator update API.
    for key in ['quarterfinal_bracket','known_knockout']:
        if state.get(key):kwargs[key]=state[key]
    return codes,first,kwargs,teams,state


def stage_table(result,codes,add_eliminated=True):
    stages=['qualified','semifinalist','finalist','champion']
    rows=[]
    for i,t in enumerate(codes):
        row={'team':t,'status':'in_swiss_at_cutoff'}
        for stage in stages:
            p=float(result['probabilities'][stage][i])
            se=float(np.sqrt(p*(1-p)/result['n']))
            row.update({stage:p,stage+'_count':int(result['counts'][stage][i]),
                        stage+'_mc_se':se,stage+'_mc_95_low':max(0,p-1.96*se),stage+'_mc_95_high':min(1,p+1.96*se)})
        rows.append(row)
    if add_eliminated:
        for t in ['MVK','FUR','C9']:
            row={'team':t,'status':'eliminated_in_play_in'}
            for stage in stages:
                for suffix in ['', '_count','_mc_se','_mc_95_low','_mc_95_high']:row[stage+suffix]=0
            rows.append(row)
    return pd.DataFrame(rows).sort_values('champion',ascending=False,kind='stable')


def simulation_phase(root,config,obs,regions,model):
    start=perf_counter();codes,first,kwargs,teams,state=tournament_inputs(root,config)
    p=model.matrix(codes)
    print('Running baseline tournament simulations...',flush=True)
    result=simulate(codes,first,p,int(config['simulation_count']),int(config['simulation_seed']),
                    draw_method=config['draw_method'],keep_examples=256,**kwargs)
    save_json(root/'outputs/simulation_base.json',result)
    table=stage_table(result,codes)
    table.to_csv(root/'outputs/stage_probabilities.csv',index=False)
    all_codes=codes+['MVK','FUR','C9']
    p_all=model.matrix(all_codes)
    for bo in [1,3,5]:
        pd.DataFrame(series_probability(p_all,bo),index=all_codes,columns=all_codes).rename_axis('row_team_beats_column').to_csv(root/f'outputs/matrix_all19_bo{bo}.csv')
    r=result['records']
    pd.DataFrame(r['probabilities'],index=codes,columns=r['labels']).rename_axis('team').to_csv(root/'outputs/swiss_records.csv')
    rd2=result['round2']
    pd.DataFrame(rd2['opponent_probabilities'],index=codes,columns=codes).rename_axis('team').to_csv(root/'outputs/round2_opponents_unconditional.csv')
    for idx,name in [(0,'given_loss'),(1,'given_win')]:
        pd.DataFrame(rd2['conditional_probabilities'][idx],index=codes,columns=codes).rename_axis('team').to_csv(root/f'outputs/round2_opponents_{name}.csv')
    nextrows=[]
    for x in state['first_round']:
        a,b=x['a'],x['b'];q=model.predict(a,b)
        nextrows.append({'match_id':x['match_id'],'team_a':a,'team_b':b,'start_bjt':x['start_bjt'],
                        'prob_a_bo1':q,'prob_b_bo1':1-q,'hypothetical_prob_a_bo3':float(series_probability(q,3)),
                        'hypothetical_prob_a_bo5':float(series_probability(q,5)),
                        'best_of':x['best_of'],'completed':x.get('completed',False),
                        'condition':'Current team-level strength, unknown future map side averaged 50:50; not a verified game-specific lineup/draft forecast.'})
    pd.DataFrame(nextrows).to_csv(root/'outputs/known_first_round_probabilities.csv',index=False)
    fp=result['final_pairs'];fp=fp['probabilities'] if isinstance(fp,dict) else fp
    finals=[]
    for i in range(16):
        for j in range(i+1,16):finals.append({'team_a':codes[i],'team_b':codes[j],'probability':float(fp[i,j])})
    pd.DataFrame(finals).sort_values('probability',ascending=False).to_csv(root/'outputs/final_pairs.csv',index=False)
    favourites=table[table.status=='in_swiss_at_cutoff'].team.tolist()[:3]
    examples=[]
    for t in favourites:
        found=next((e for e in result['examples'] if e['champion']==t),None)
        if found:examples.append({'selection_reason':f'First stored actual realization won by {t}; not a modal path.','path':found})
    for t in favourites[:2]:
        found=next((e for e in result['examples'] if str(e['swiss_final_records'][t]).startswith(('0-','1-','2-'))),None)
        if found:examples.append({'selection_reason':f'First stored actual realization where {t} is eliminated in Swiss; not the most likely exit.','path':found})
    save_json(root/'outputs/representative_examples.json',examples)
    save_json(root/'outputs/simulation_checks.json',{'baseline':result['checks'],
       'pairwise_complement_error':float(np.max(np.abs(p+p.T-1))),
       'all19_probability_sums':{s:float(table[s].sum()) for s in ['qualified','semifinalist','finalist','champion']},
       'probability_monotonicity_all19':bool(((table.champion<=table.finalist)&(table.finalist<=table.semifinalist)&(table.semifinalist<=table.qualified)).all())})
    print(table[['team','qualified','semifinalist','finalist','champion']].to_string(index=False),flush=True)
    # Every scenario is explicitly changed from the primary model; no post-hoc
    # narrative adjustments or subjective pre-chat probabilities are inputs.
    scenarios=[('baseline','Primary fitted model',model,config['draw_method'],None),
               ('half_life_30','30-day recency stress, other parameters fixed',fit_bt(obs,config['cutoff_utc'],30,model.ridge,model.temperature),config['draw_method'],None),
               ('half_life_365','365-day recency stress, other parameters fixed',fit_bt(obs,config['cutoff_utc'],365,model.ridge,model.temperature),config['draw_method'],None),
               ('international_half_weight','Known cross-region maps weighted 0.5x',fit_bt(obs,config['cutoff_utc'],model.half_life,model.ridge,model.temperature,international_multiplier=.5,region_map=regions),config['draw_method'],None),
               ('international_double_weight','Known cross-region maps weighted 2x',fit_bt(obs,config['cutoff_utc'],model.half_life,model.ridge,model.temperature,international_multiplier=2,region_map=regions),config['draw_method'],None),
               ('uniform_legal_draw','Alternative draw distribution; same strength matrix',model,'uniform_valid',None)]
    riot_obs,_,_=prepare(root,config['cutoff_utc'],'riot_only')
    extended,_,_=prepare(root,config['cutoff_utc'],'all_selected')
    scenarios.extend([
        ('exclude_EWC','Drop EWC main as event-composition sensitivity',fit_bt(riot_obs,config['cutoff_utc'],model.half_life,model.ridge,model.temperature),config['draw_method'],None),
        ('extended_events','Include EWC qualifiers and DCGI, without retuning',fit_bt(extended,config['cutoff_utc'],model.half_life,model.ridge,model.temperature),config['draw_method'],None),
        ('lpl_strength_plus_0p30','Assumed unobserved region drift: LPL +0.30 logit, not estimated',model,config['draw_method'],np.array([.30 if regions.get(t)=='LPL' else 0 for t in codes])),
        ('lck_strength_plus_0p30','Assumed unobserved region drift: LCK +0.30 logit, not estimated',model,config['draw_method'],np.array([.30 if regions.get(t)=='LCK' else 0 for t in codes])),
        ('tes_roster_minus_0p30','TES roster stress: -0.30 logit, not measured player impact',model,config['draw_method'],np.array([-.30 if t=='TES' else 0 for t in codes])),
        ('tes_roster_plus_0p30','TES roster stress: +0.30 logit, not measured player impact',model,config['draw_method'],np.array([.30 if t=='TES' else 0 for t in codes]))])
    records=[];scenario_info=[]
    for i,(name,description,fitted,draw,offset) in enumerate(scenarios):
        seed=int(config['simulation_seed']) # common random numbers reduce pure comparison noise
        res=result if name=='baseline' else simulate(codes,first,fitted.matrix(codes,offset),int(config['sensitivity_count_each']),seed,draw_method=draw,keep_examples=0,**kwargs)
        for j,t in enumerate(codes):
            records.append({'scenario':name,'team':t,**{s:float(res['probabilities'][s][j]) for s in ['qualified','semifinalist','finalist','champion']}})
        winner=int(np.argmax(res['probabilities']['champion']))
        scenario_info.append({'scenario':name,'description':description,'n_completed':res['n'],'seed':seed,
             'leader':codes[winner],'leader_champion_probability':float(res['probabilities']['champion'][winner]),
             'draw_method':draw,'half_life':fitted.half_life,'model_is_refit':fitted is not model,
             'all_invariants_passed':True})
        print(f'Scenario {name}: {codes[winner]} {res["probabilities"]["champion"][winner]:.4f}',flush=True)
    for rho in [.10,.25]:
        name=f'series_correlation_{rho:.2f}'
        res=simulate(codes,first,p,int(config['sensitivity_count_each']),int(config['simulation_seed']),
                     draw_method=config['draw_method'],keep_examples=0,series_correlation=rho,**kwargs)
        for j,t in enumerate(codes):
            records.append({'scenario':name,'team':t,**{s:float(res['probabilities'][s][j]) for s in ['qualified','semifinalist','finalist','champion']}})
        winner=int(np.argmax(res['probabilities']['champion']))
        scenario_info.append({'scenario':name,'description':f'Unestimated intra-series exchangeable Bernoulli correlation rho={rho}; Beta-mixture stress scenario, unchanged mean map probability.',
             'n_completed':res['n'],'seed':config['simulation_seed'],'leader':codes[winner],
             'leader_champion_probability':float(res['probabilities']['champion'][winner]),
             'draw_method':config['draw_method'],'half_life':model.half_life,'model_is_refit':False,
             'all_invariants_passed':True})
        print(f'Scenario {name}: {codes[winner]} {res["probabilities"]["champion"][winner]:.4f}',flush=True)
    pd.DataFrame(records).to_csv(root/'outputs/sensitivity_probabilities.csv',index=False)
    pd.DataFrame(scenario_info).to_csv(root/'outputs/sensitivity_summary.csv',index=False)
    save_json(root/'outputs/simulation_log.json',{'completed':True,'baseline_completed':int(result['n']),
       'sensitivity_scenarios_including_baseline':len(scenario_info),'additional_completed':sum(s['n_completed'] for s in scenario_info[1:]),
       'seconds':perf_counter()-start,'seed':config['simulation_seed'],'draw_distribution_status':'Operational approximation of published slot language, not certified Riot internal implementation',
       'scenarios':scenario_info})


def uncertainty_phase(root,config,obs,regions,model):
    start=perf_counter();codes,first,kwargs,_,_=tournament_inputs(root,config)
    rng=np.random.default_rng(config['bootstrap_seed'])
    clusters=sorted(obs.series_id.unique())
    b=int(config['bootstrap_replicates']);n=int(config['bootstrap_simulations_each'])
    rows=[];pair_draws=[];top_strength_counts={t:0 for t in codes};logs=[]
    print(f'Weighted cluster bootstrap: {b} refits x {n} tournaments...',flush=True)
    for k in range(b):
        weights=rng.exponential(1,size=len(clusters));weights/=weights.mean()
        fitted=fit_bt(obs,config['cutoff_utc'],model.half_life,model.ridge,model.temperature,
                      cluster_weights=dict(zip(clusters,weights)))
        p=fitted.matrix(codes);pair_draws.append(p)
        seed=int(rng.integers(0,2**32-1))
        res=simulate(codes,first,p,n,seed,draw_method=config['draw_method'],keep_examples=0,**kwargs)
        top=codes[int(np.argmax(res['probabilities']['champion']))];top_strength_counts[top]+=1
        for i,t in enumerate(codes):
            rows.append({'replicate':k,'team':t,'seed':seed,'strength_logit':fitted.strength(t),
                         **{s:float(res['probabilities'][s][i]) for s in ['qualified','semifinalist','finalist','champion']}})
        logs.append({'replicate':k,'seed':seed,'completed':res['n'],'side_beta':fitted.side_beta,'leader':top})
        if (k+1)%20==0:print(f'  completed {k+1}/{b}',flush=True)
    samples=pd.DataFrame(rows);samples.to_csv(root/'outputs/bootstrap_samples.csv',index=False)
    summary=[]
    for t in codes+['MVK','FUR','C9']:
        subset=samples[samples.team==t];entry={'team':t}
        for stage in ['qualified','semifinalist','finalist','champion']:
            values=subset[stage].to_numpy() if len(subset) else np.zeros(b)
            q=np.quantile(values,[.05,.5,.95])
            entry.update({stage+'_q05':float(q[0]),stage+'_q50':float(q[1]),stage+'_q95':float(q[2]),
                          stage+'_bootstrap_mean':float(values.mean())})
        entry['bootstrap_leader_fraction']=top_strength_counts.get(t,0)/b;summary.append(entry)
    pd.DataFrame(summary).to_csv(root/'outputs/bootstrap_summary.csv',index=False)
    pp=np.asarray(pair_draws)
    for bo in [1,3,5]:
        bops=series_probability(pp,bo)
        for q,name in [(.05,'q05'),(.5,'q50'),(.95,'q95')]:
            pd.DataFrame(np.quantile(bops,q,axis=0),index=codes,columns=codes).rename_axis('row_team_beats_column').to_csv(root/f'outputs/matrix_bo{bo}_{name}.csv')
    save_json(root/'outputs/bootstrap_log.json',{'completed':True,'replicates_completed':b,
       'tournaments_each':n,'total_tournaments_completed':sum(x['completed'] for x in logs),'seed':config['bootstrap_seed'],
       'seconds':perf_counter()-start,'method':config['bootstrap_method'],'interval_label':config['uncertainty_label'],
       'hyperparameters_fixed':True,'inner_simulation_max_mc_se':.5/np.sqrt(n),
       'not_covered':['Dataset systematic errors','Team-specific patch effects','Unrecorded lineup changes','Model selection uncertainty','Non-IID game effects','Unknown official draw implementation'],
       'replicate_logs':logs})
    print('Bootstrap completed.',flush=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--phase',choices=['fit','simulate','uncertainty','all'],default='all')
    parser.add_argument('--root',type=Path,default=ROOT)
    args=parser.parse_args();root=args.root.resolve()
    config=json.loads((root/'config.json').read_text())
    for d in ['outputs','figures','docs']: (root/d).mkdir(parents=True,exist_ok=True)
    obs,regions,audit=prepare(root,config['cutoff_utc'],config['primary_selection'])
    obs.to_csv(root/'data/model_observations.csv',index=False)
    save_json(root/'outputs/data_audit.json',audit)
    save_json(root/'outputs/environment.json',{
        'python':platform.python_version(),
        'packages':{x:importlib.metadata.version(x) for x in ['numpy','pandas','scipy','matplotlib']},
        'execution_time_utc':datetime.now(timezone.utc).isoformat(),
        'information_cutoff_utc':config['cutoff_utc']})
    if args.phase in ('fit','all'): model=fit_phase(root,config,obs,regions,audit)
    else: model=load_model(root/'outputs/fitted_model.json')
    if args.phase in ('simulate','all'): simulation_phase(root,config,obs,regions,model)
    if args.phase in ('uncertainty','all'): uncertainty_phase(root,config,obs,regions,model)
    if args.phase in ('simulate','all') and (root/'data/historical_lineups.csv').exists():
        from lineup_sensitivity import run
        run(root)


if __name__=='__main__':main()
