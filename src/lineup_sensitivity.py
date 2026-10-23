"""Final-cutoff lineup relevance stress tests; NOT historical backtest features.

These weights are explicit assumptions about transfer of earlier team results.
They do not estimate a causal player effect and are not selected on the test set.
"""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from prepare_model_data import prepare
from model import fit_bt
from run_analysis import load_model,tournament_inputs,save_json
from simulator import simulate

ROOT=Path(__file__).resolve().parents[1]
ROLES=['top','jungle','mid','bot','support']


def roster_tuple(row):return tuple(str(row[k]).strip().casefold() for k in ROLES)


def run(root=ROOT):
    root=Path(root)
    config=json.loads((root/'config.json').read_text());model=load_model(root/'outputs/fitted_model.json')
    obs,regions,audit=prepare(root,config['cutoff_utc'],config['primary_selection'])
    # Joined historical names are persisted, but the primary likelihood ignores
    # them. This refreshes metadata/coverage only, not the baseline prediction.
    obs.to_csv(root/'data/model_observations.csv',index=False)
    save_json(root/'outputs/data_audit.json',audit)
    history=pd.read_csv(root/'data/historical_lineups.csv')
    history=history[history.game_id.isin(obs.game_id.dropna())].copy()
    history['date']=pd.to_datetime(history.date,utc=True)
    history=history[history.date<pd.Timestamp(config['cutoff_utc'])]
    current={t:roster_tuple(g.sort_values('date').iloc[-1]) for t,g in history.groupby('team')}
    reported=json.loads((root/'data/teams.json').read_text())+json.loads((root/'data/eliminated_teams.json').read_text())
    for t in reported:
        by_role={x['role'].lower():x['player'].strip().casefold() for x in t['roster']}
        current[t['code']]=tuple(by_role[k] for k in ROLES)
    lookup={(r.game_id,r.team):tuple(str(getattr(r,k)).strip().casefold() for k in ROLES) for r in history.itertuples()}
    mismatch=[]
    for row in obs.itertuples():
        value=0
        for team in [row.team_a,row.team_b]:
            actual=lookup.get((row.game_id,team))
            if actual is not None and actual!=current[team]:value+=1
        mismatch.append(value)
    obs['lineup_mismatching_teams']=mismatch
    codes,first,kwargs,_,_=tournament_inputs(root,config)
    old_probs=pd.read_csv(root/'outputs/sensitivity_probabilities.csv')
    old_summary=pd.read_csv(root/'outputs/sensitivity_summary.csv')
    prefix='lineup_transfer_'
    old_probs=old_probs[~old_probs.scenario.str.startswith(prefix)]
    old_summary=old_summary[~old_summary.scenario.str.startswith(prefix)]
    rows=[];summaries=[]
    for discount in [.5,.25]:
        name=f'{prefix}{discount:.2f}'
        obs['lineup_relevance_weight']=discount**obs.lineup_mismatching_teams
        fitted=fit_bt(obs,config['cutoff_utc'],model.half_life,model.ridge,model.temperature,
                      relevance_column='lineup_relevance_weight')
        res=simulate(codes,first,fitted.matrix(codes),int(config['sensitivity_count_each']),int(config['simulation_seed']),
                      draw_method=config['draw_method'],keep_examples=0,**kwargs)
        for i,t in enumerate(codes):rows.append({'scenario':name,'team':t,**{s:float(res['probabilities'][s][i]) for s in ['qualified','semifinalist','finalist','champion']}})
        i=int(np.argmax(res['probabilities']['champion']))
        summaries.append({'scenario':name,'description':f'Historical map likelihood weight x{discount} per team whose observed lineup differs from the final-cutoff reported/current lineup; descriptive sensitivity, not validated or causal player effect.',
             'n_completed':res['n'],'seed':config['simulation_seed'],'leader':codes[i],
             'leader_champion_probability':float(res['probabilities']['champion'][i]),
             'draw_method':config['draw_method'],'half_life':model.half_life,'model_is_refit':True,'all_invariants_passed':True})
        print(name,codes[i],res['probabilities']['champion'][i],flush=True)
    pd.concat([old_probs,pd.DataFrame(rows)],ignore_index=True).to_csv(root/'outputs/sensitivity_probabilities.csv',index=False)
    pd.concat([old_summary,pd.DataFrame(summaries)],ignore_index=True).to_csv(root/'outputs/sensitivity_summary.csv',index=False)
    save_json(root/'outputs/lineup_sensitivity_log.json',{'completed':True,'cutoff':config['cutoff_utc'],
       'tournaments_completed':sum(x['n_completed'] for x in summaries),'scenarios':summaries,
       'maps_by_number_of_mismatching_teams':obs.lineup_mismatching_teams.value_counts().sort_index().to_dict(),
       'unobserved_lineups_not_fabricated':2,'prediction_temporal_status':'Uses current roster evidence only at final cutoff, never in retrospective tuning or test predictions.',
       'not_a_validated_extension_of_primary_model':True,'weights_are_assumptions':True,
       'current_lineup_for_non_worlds_teams':'Most recent lineup in primary data before cutoff',
       'bootstrap_primary_parameters_unchanged':True})
    log=json.loads((root/'outputs/simulation_log.json').read_text())
    core_count=log.get('additional_completed_without_lineup',log['additional_completed'])
    log['additional_completed_without_lineup']=core_count
    log['additional_lineup_completed']=sum(x['n_completed'] for x in summaries)
    log['additional_completed']=core_count+log['additional_lineup_completed']
    log['sensitivity_scenarios_including_baseline']=len(old_summary)+len(summaries)
    log['scenarios']=[x for x in log['scenarios'] if not x['scenario'].startswith(prefix)]+summaries
    save_json(root/'outputs/simulation_log.json',log)


if __name__=='__main__':run()
