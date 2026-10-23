"""Build explicit modeling observations without inventing missing map metadata."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from model import clean_history


def prepare(root: Path, cutoff: str, selection='major_leagues_and_international_mains'):
    games=pd.read_csv(root/'data/history_games.csv',dtype={'patch':str})
    supplements=pd.read_csv(root/'data/history_supplements.csv',dtype={'patch':str})
    games['observation_type']='actual_map'
    games['side_a']=np.where(games.team_a_is_blue==1,'blue','red')
    games['game_date']=games.date
    games['map_count']=games.wins_a+games.wins_b
    # Residual counts alone supplement the genuinely missing outcomes. They use
    # the SAME cluster as existing maps; never add the full parent series score.
    if len(supplements):
        if not supplements.observation_type.eq('official_series_residual').all():
            raise ValueError('Only reconciled residual supplements accepted')
        supplements['official_series_id']=supplements.series_id
        supplements['series_id']=supplements.source_series_id
        if not set(supplements.series_id).issubset(set(games.series_id)):
            raise ValueError('Residual supplement has no observed parent series')
        supplements['side_a']='unknown'
        supplements['game_date']=pd.NA
        supplements['event_scope']='regional'
        supplements['map_count']=supplements.wins_a+supplements.wins_b
        # A conservative availability bound for missing map timestamps. Exact
        # minute and side stay missing; these are not invented map rows.
        supplements['start_plus_duration_utc']=(pd.to_datetime(supplements.date,utc=True)+pd.Timedelta(hours=6)).dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    obs=pd.concat([games,supplements],ignore_index=True,sort=False)
    if selection=='major_leagues_and_international_mains':
        obs=obs[(obs.competition!='DCGI') & (obs.event_scope!='EWC_qualifier')].copy()
    elif selection=='riot_only':
        obs=obs[obs.competition!='EWC'].copy()
        obs=obs[obs.competition!='DCGI'].copy()
    elif selection!='all_selected':
        raise ValueError(selection)
    obs['source_date']=obs.date
    obs['date']=pd.to_datetime(obs.date,utc=True)
    obs['series_end']=pd.to_datetime(obs.start_plus_duration_utc,utc=True)
    obs['date']=obs.groupby('series_id').date.transform('min')
    obs['series_end']=obs.groupby('series_id').series_end.transform('max')
    obs=obs[obs.series_end<pd.Timestamp(cutoff)].copy()
    obs=clean_history(obs)
    lineup_file=root/'data/historical_lineups.csv'
    if lineup_file.exists():
        lineups=pd.read_csv(lineup_file)
        if lineups.duplicated(['game_id','team']).any():raise ValueError('Duplicate historical team-game lineup')
        for side in ['a','b']:
            cols=['game_id','team','top','jungle','mid','bot','support','lineup_id']
            lookup=lineups[cols].rename(columns={'team':f'team_{side}',**{k:f'{side}_{k}' for k in cols[2:]}})
            obs=obs.merge(lookup,how='left',on=['game_id',f'team_{side}'],validate='many_to_one')
        roster_missing=obs[['a_lineup_id','b_lineup_id']].isna().any(axis=1)
    else:
        roster_missing=pd.Series(True,index=obs.index)
    if obs[obs.observation_type.eq('actual_map')].game_id.duplicated().any():
        raise AssertionError('Duplicate map IDs')
    teams=pd.read_csv(root/'data/history_teams.csv')
    regions=dict(zip(teams.team,teams.region))
    obs['region_a']=obs.team_a.map(regions).fillna('UNKNOWN')
    obs['region_b']=obs.team_b.map(regions).fillna('UNKNOWN')
    obs['cross_region']=(obs.region_a!=obs.region_b)&(obs.region_a!='UNKNOWN')&(obs.region_b!='UNKNOWN')
    # Verify every primary team lies in one connected comparison graph.
    codes=sorted(set(obs.team_a)|set(obs.team_b)); neighbours={t:set() for t in codes}
    for a,b in zip(obs.team_a,obs.team_b): neighbours[a].add(b);neighbours[b].add(a)
    unseen=set(codes); components=[]
    while unseen:
        frontier=[next(iter(unseen))];component=set()
        while frontier:
            t=frontier.pop()
            if t in component:continue
            component.add(t);frontier.extend(neighbours[t]-component)
        unseen-=component;components.append(sorted(component))
    report={
        'selection':selection,'cutoff_utc':cutoff,'rows':len(obs),
        'actual_map_rows':int(obs.observation_type.eq('actual_map').sum()),
        'residual_count_rows':int(obs.observation_type.ne('actual_map').sum()),
        'total_map_outcomes':int(obs.map_count.sum()),'series':int(obs.series_id.nunique()),'teams':len(codes),
        'first_series_start':str(obs.date.min()),'last_series_end':str(obs.series_end.max()),
        'known_cross_region_maps':int(obs.loc[obs.cross_region,'map_count'].sum()),
        'known_cross_region_series':int(obs.loc[obs.cross_region,'series_id'].nunique()),
        'unknown_region_maps':int(obs.loc[(obs.region_a=='UNKNOWN')|(obs.region_b=='UNKNOWN'),'map_count'].sum()),
        'side_missing_maps':int(obs.loc[obs.side_a.eq('unknown'),'map_count'].sum()),
        'patch_missing_maps':int(obs.loc[obs.patch.isna(),'map_count'].sum()),
        'historical_starting_roster_missing_fraction':float(obs.loc[roster_missing,'map_count'].sum()/obs.map_count.sum()),
        'historical_roster_features_used_by_primary_model':False,
        'connected_components':components,
        'competition_counts':obs.groupby('competition').map_count.sum().astype(int).to_dict(),
        'notes':[
            'Actual map records and residual count observations are explicitly distinguished.',
            'No game metrics, current final ranking, or later roster annotation enters features.',
            'Series start and availability end are used to purge boundary-crossing series.',
            'Historical game version labels are source labels, e.g. 16.20; not silently rewritten as official 26.20.',
            'Event-time retrospective backtest, using this frozen source snapshot; not archival snapshots collected before every historical prediction.',
            'Primary excludes EWC qualifiers and DCGI; broader selection is a separate sensitivity scenario.'
        ]
    }
    return obs,regions,report


if __name__=='__main__':
    root=Path(__file__).resolve().parents[1]
    config=json.loads((root/'config.json').read_text())
    obs,_,report=prepare(root,config['cutoff_utc'])
    obs.to_csv(root/'data/model_observations.csv',index=False)
    (root/'outputs/data_audit.json').write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps(report,ensure_ascii=False,indent=2))
