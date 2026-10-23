"""Independent arithmetic and first-round analytic checks for saved results."""
from pathlib import Path
import json
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]


def main():
    report={}
    stage=pd.read_csv(ROOT/'outputs/stage_probabilities.csv')
    assert len(stage)==19 and stage.team.is_unique
    for col,total in [('qualified',8),('semifinalist',4),('finalist',2),('champion',1)]:
        assert abs(stage[col].sum()-total)<1e-12
    assert ((stage.qualified>=stage.semifinalist)&(stage.semifinalist>=stage.finalist)&(stage.finalist>=stage.champion)).all()
    assert stage[stage.status=='eliminated_in_play_in'][['qualified','semifinalist','finalist','champion']].eq(0).all().all()
    report['all19_seat_sums']={c:float(stage[c].sum()) for c in ['qualified','semifinalist','finalist','champion']}
    for bo in [1,3,5]:
        x=pd.read_csv(ROOT/f'outputs/matrix_all19_bo{bo}.csv',index_col=0)
        assert x.shape==(19,19)
        assert np.max(np.abs(x.values+x.values.T-1))<1e-12
        assert np.allclose(np.diag(x),.5)
    report['three_all19_matrices_complementary']=True
    obs=pd.read_csv(ROOT/'data/model_observations.csv')
    assert obs[obs.observation_type=='actual_map'].game_id.is_unique
    assert obs.loc[obs.observation_type=='official_series_residual','series_id'].isin(obs.loc[obs.observation_type=='actual_map','series_id']).all()
    pred=pd.read_csv(ROOT/'outputs/backtest_predictions.csv')
    assert pred.groupby('series_id').fold_start.nunique().max()==1
    assert pred.groupby('series_id').p_bt.nunique().max()==1
    assert pred.groupby('series_id').p_elo.nunique().max()==1
    report['prediction_series_not_split_or_updated_midseries']=True
    report['observed_actual_maps']=int(obs.observation_type.eq('actual_map').sum())
    report['residual_count_rows']=int(obs.observation_type.eq('official_series_residual').sum())
    calibration=pd.read_csv(ROOT/'outputs/calibration.csv')
    assert calibration.map_weight.sum()==len(pred)
    report['calibration_map_count']=int(calibration.map_weight.sum())
    # Independent analytic round-2 calculation: given the opening result, the
    # seven other winners/losers are drawn without any in-pool rematch edges.
    # Each candidate has 1/7 pairing probability if in the same result group.
    known=pd.read_csv(ROOT/'outputs/known_first_round_probabilities.csv')
    pwin={};previous={}
    for r in known.itertuples():
        pwin[r.team_a]=r.prob_a_bo1;pwin[r.team_b]=1-r.prob_a_bo1
        previous[r.team_a]=r.team_b;previous[r.team_b]=r.team_a
    uncond=pd.read_csv(ROOT/'outputs/round2_opponents_unconditional.csv',index_col=0)
    codes=list(uncond.index)
    win=np.zeros((16,16));loss=win.copy();total=win.copy()
    for i,a in enumerate(codes):
        for j,b in enumerate(codes):
            if a==b or previous[a]==b:continue
            win[i,j]=pwin[b]/7;loss[i,j]=(1-pwin[b])/7
            total[i,j]=pwin[a]*win[i,j]+(1-pwin[a])*loss[i,j]
    assert np.allclose(win.sum(axis=1),1) and np.allclose(loss.sum(axis=1),1)
    assert np.allclose(total,total.T) and np.allclose(total.sum(axis=1),1)
    for name,value in [('unconditional',total),('given_win',win),('given_loss',loss)]:
        pd.DataFrame(value,index=codes,columns=codes).rename_axis('team').to_csv(ROOT/f'outputs/round2_analytic_{name}.csv')
    n=json.loads((ROOT/'outputs/simulation_log.json').read_text())['baseline_completed']
    se=np.sqrt(total*(1-total)/n)
    nonzero=se>0
    maxz=float(np.max(np.abs(uncond.values-total)[nonzero]/se[nonzero]))
    assert maxz<6,('Unexpected round-2 MC mismatch',maxz)
    report['round2_vs_independent_analytic_max_standard_errors']=maxz
    report['round2_analytic_condition']='All 16 teams are 0-0 at the frozen cutoff; later updated states require a different analytic check.'
    report['all_checks_passed']=True
    (ROOT/'outputs/independent_validation.json').write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps(report,indent=2,ensure_ascii=False))


if __name__=='__main__':main()
