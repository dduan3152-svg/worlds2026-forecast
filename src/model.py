"""Auditable time-decayed Bradley-Terry baseline.

Accepts genuine map rows (wins_a/wins_b in {0,1}) or aggregate series scores.
Aggregate scores are never expanded into invented game orders. All predictions
for a series are made before any result from that series updates the model.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import expit


def series_probability(p, best_of=1):
    p = np.asarray(p, dtype=float)
    if best_of == 1:
        return p
    if best_of == 3:
        return 3 * p ** 2 - 2 * p ** 3
    if best_of == 5:
        return 10 * p ** 3 - 15 * p ** 4 + 6 * p ** 5
    raise ValueError(f"Unsupported best-of: {best_of}")


def clean_history(frame):
    d = frame.copy()
    required = ['series_id', 'date', 'team_a', 'team_b', 'wins_a', 'wins_b']
    absent = [c for c in required if c not in d]
    if absent:
        raise ValueError(f"Missing required history fields: {absent}")
    d['date'] = pd.to_datetime(d['date'], utc=True)
    for c in ['wins_a', 'wins_b']:
        d[c] = pd.to_numeric(d[c], errors='raise').astype(int)
    if (d[['wins_a', 'wins_b']] < 0).any().any():
        raise ValueError('Negative map score')
    if (d.team_a == d.team_b).any():
        raise ValueError('Self match in history')
    if ((d.wins_a + d.wins_b) <= 0).any():
        raise ValueError('Empty series')
    if 'series_end' in d:
        d['series_end'] = pd.to_datetime(d['series_end'], utc=True)
    return d.sort_values(['date', 'series_id'], kind='stable').reset_index(drop=True)


def split_fold(frame, start, end):
    """Complete series stay together; a series straddling a boundary is purged."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    finish = frame.series_end if 'series_end' in frame else frame.date
    train = frame[(frame.date < start) & (finish < start)]
    valid = frame[(frame.date >= start) & (finish < end)]
    if set(train.series_id) & set(valid.series_id):
        raise AssertionError('Series leakage across training/test boundary')
    return train, valid


@dataclass
class BTModel:
    teams: list
    theta: np.ndarray
    half_life: float
    ridge: float
    cutoff: str
    temperature: float = 1.0
    success: bool = True
    n_rows: int = 0
    n_maps: int = 0
    effective_maps: float = 0.0
    side_beta: float = 0.0

    def strength(self, code):
        return float(self.theta[self.teams.index(code)]) if code in self.teams else 0.0

    def predict(self, team_a, team_b):
        delta = self.strength(team_a)-self.strength(team_b)
        return float((expit((delta+self.side_beta)/self.temperature)
                      + expit((delta-self.side_beta)/self.temperature))/2)

    def matrix(self, codes, offsets=None):
        t = np.array([self.strength(c) for c in codes])
        if offsets is not None:
            t = t + np.asarray(offsets)
        delta=t[:, None]-t[None, :]
        return (expit((delta+self.side_beta)/self.temperature)
                +expit((delta-self.side_beta)/self.temperature))/2

    def as_dict(self):
        return {**self.__dict__, 'theta': self.theta.tolist(),
                'equation': 'P(A wins map)=mean_s in {-1,+1} sigmoid((theta_A-theta_B+s*beta_blue)/temperature)',
                'penalty': 'ridge/2 * (sum(theta_team**2) + beta_blue**2)',
                'age_weight': '2**(-age_days/half_life)',
                'side_assumption': 'Historical blue coefficient fit where available; unknown future side marginalised 50:50. First Selection preferences not estimated.',
                'series_assumption': 'IID conditional on fixed team strengths within a series'}


def fit_bt(frame, cutoff, half_life=120., ridge=1., temperature=1.,
           cluster_weights=None, international_multiplier=1., region_map=None,
           relevance_column=None):
    d = clean_history(frame)
    ct = pd.Timestamp(cutoff)
    if ct.tzinfo is None:
        ct = ct.tz_localize('UTC')
    d = d[(d.date < ct) & ((d.series_end < ct) if 'series_end' in d else True)].copy()
    if len(d) == 0:
        raise ValueError('No observations before training cutoff')
    teams = sorted(set(d.team_a) | set(d.team_b))
    lookup = {t: i for i, t in enumerate(teams)}
    ia = d.team_a.map(lookup).to_numpy()
    ib = d.team_b.map(lookup).to_numpy()
    wa, wb = d.wins_a.to_numpy(float), d.wins_b.to_numpy(float)
    nm = wa + wb
    age = (ct-d.date).dt.total_seconds().to_numpy()/86400.
    weight = np.exp2(-age/half_life)
    if relevance_column is not None:
        relevance=d[relevance_column].to_numpy(float)
        if not np.isfinite(relevance).all() or (relevance<=0).any():
            raise ValueError('Relevance weights must be finite and positive')
        weight*=relevance
    if 'side_a' in d:
        side = d.side_a.astype(str).str.lower().map({'blue':1.,'red':-1.}).fillna(0.).to_numpy()
    else:
        side = np.zeros(len(d))
    has_side = bool(np.any(side))
    if cluster_weights is not None:
        weight *= d.series_id.map(cluster_weights).fillna(1.).to_numpy(float)
    if international_multiplier != 1.:
        reg = region_map or {}
        cross = np.array([reg.get(a) not in (None,'','UNKNOWN') and reg.get(b) not in (None,'','UNKNOWN') and reg[a] != reg[b]
                          for a, b in zip(d.team_a, d.team_b)])
        weight[cross] *= international_multiplier

    def objective(theta):
        z = theta[ia] - theta[ib] + (theta[-1]*side if has_side else 0)
        loss = np.sum(weight * (nm*np.logaddexp(0, z)-wa*z)) + ridge*np.dot(theta, theta)/2
        residual = weight * (nm*expit(z)-wa)
        grad = (np.bincount(ia, weights=residual, minlength=len(theta))
                - np.bincount(ib, weights=residual, minlength=len(theta)) + ridge*theta)
        if has_side:
            grad[-1] += np.dot(residual,side)
        return float(loss), grad

    result = minimize(objective, np.zeros(len(teams)+int(has_side)), jac=True, method='L-BFGS-B',
                      options={'maxiter': 500, 'ftol': 1e-11, 'gtol': 1e-7})
    if not result.success:
        raise RuntimeError(f'BT fit failed: {result.message}')
    return BTModel(teams, result.x[:len(teams)], half_life, ridge, str(ct), temperature,
                   True, len(d), int(nm.sum()), float(np.dot(weight, nm)),
                   float(result.x[-1]) if has_side else 0.)


def predict_frame(model, frame):
    return np.array([model.predict(a, b) for a, b in zip(frame.team_a, frame.team_b)])


def probability_metrics(frame, p):
    p = np.clip(np.asarray(p, dtype=float), 1e-12, 1-1e-12)
    wa, wb = frame.wins_a.to_numpy(float), frame.wins_b.to_numpy(float)
    n = float((wa+wb).sum())
    correct = np.where(p > .5, wa, np.where(p < .5, wb, (wa+wb)/2))
    return {'rows': int(len(frame)), 'series': int(frame.series_id.nunique()),
            'map_outcomes': int(n), 'accuracy': float(correct.sum()/n),
            'brier': float((wa*(1-p)**2+wb*p**2).sum()/n),
            'log_loss': float(-(wa*np.log(p)+wb*np.log1p(-p)).sum()/n),
            'tie_scoring': 'Predicted p=0.5 receives expected 50% accuracy'}


def calibration_table(frame, p, bins=5):
    """Reliability of the forecast favorite, one original map outcome per row.

    Unlike mirroring both team perspectives into symmetric bins, this avoids
    forcing the central bin to be exactly 50% by construction. The outcome is
    whether the forecast favorite won, not whether alphabetical team A won.
    """
    p = np.asarray(p)
    wa, wb = frame.wins_a.to_numpy(float), frame.wins_b.to_numpy(float)
    pp = np.maximum(p,1-p)
    successes = np.where(p>.5,wa,np.where(p<.5,wb,(wa+wb)/2))
    total = wa+wb
    cuts = np.linspace(.5, 1, bins+1)
    records=[]
    for lo, hi in zip(cuts[:-1], cuts[1:]):
        m=(pp>=lo)&(pp<(hi if hi<1 else 1.00000001))
        nn=total[m].sum()
        records.append({'bin_low':lo,'bin_high':hi,'map_weight':int(nn),
                        'series_count':int(frame.loc[m,'series_id'].nunique()),
                        'mean_prediction':float(np.dot(pp[m],total[m])/nn) if nn else None,
                        'observed_win_rate':float(successes[m].sum()/nn) if nn else None,
                        'note':'Forecast-favorite perspective; maps in one series are correlated, not independent'})
    return pd.DataFrame(records)


def batch_elo_predictions(frame, k=20., scale=400.):
    """Basic Elo comparator with one batch update after each complete series.

    No unknown within-series order is invented; all maps in one series get
    that series' pre-match probability. K is per map-equivalent residual.
    """
    d=clean_history(frame)
    ratings={}
    out=np.empty(len(d))
    for sid, grp in d.groupby('series_id',sort=False):
        pending={}
        for idx,row in grp.iterrows():
            a,b=row.team_a,row.team_b
            p=1/(1+10**((ratings.get(b,1500)-ratings.get(a,1500))/scale))
            out[idx]=p
            delta=k*(row.wins_a-(row.wins_a+row.wins_b)*p)
            pending[a]=pending.get(a,0.)+delta
            pending[b]=pending.get(b,0.)-delta
        for t,delta in pending.items():
            ratings[t]=ratings.get(t,1500.)+delta
    return out,ratings


def choose_hyperparameters(frame, folds, grid_half=(30.,60.,120.,365.),
                           grid_ridge=(.25,1.,4.), temperatures=(.8,1.,1.2,1.5)):
    rows=[]
    for half in grid_half:
        for ridge in grid_ridge:
            blocks=[]
            fitted_models=[]
            for start,end in folds:
                train,valid=split_fold(frame,start,end)
                if len(train)<20 or len(valid)<5:
                    continue
                fitted=fit_bt(train,start,half,ridge)
                fitted_models.append(fitted)
                blocks.append(valid)
            if not blocks:
                continue
            vv=pd.concat(blocks,ignore_index=True)
            for temp in temperatures:
                pp=np.concatenate([predict_frame(replace(m,temperature=temp),v)
                                   for m,v in zip(fitted_models,blocks)])
                m=probability_metrics(vv,pp)
                rows.append({'half_life':half,'ridge':ridge,'temperature':temp,**m})
    result=pd.DataFrame(rows)
    if result.empty:
        return {'half_life':120.,'ridge':1.,'temperature':1.,'selection':'fixed_unvalidated_default'},result
    result=result.sort_values(['log_loss','brier'],kind='stable').reset_index(drop=True)
    best=result.iloc[0]
    return {k:float(best[k]) for k in ['half_life','ridge','temperature']},result


def rolling_evaluate(frame, folds, parameters):
    preds=[]
    summaries=[]
    for start,end in folds:
        train,valid=split_fold(frame,start,end)
        valid=valid.copy()
        if len(train)<20 or len(valid)==0:
            continue
        fitted=fit_bt(train,start,**{k:parameters[k] for k in ['half_life','ridge','temperature']})
        valid['p_bt']=predict_frame(fitted,valid)
        _,ratings=batch_elo_predictions(train)
        valid['p_elo']=[1/(1+10**((ratings.get(b,1500)-ratings.get(a,1500))/400))
                        for a,b in zip(valid.team_a,valid.team_b)]
        valid['p_half']=.5
        valid['fold_start']=start
        valid['fold_end']=end
        preds.append(valid)
        for label,column in [('BT','p_bt'),('BatchElo','p_elo'),('Half','p_half')]:
            summaries.append({'fold_start':start,'fold_end':end,'model':label,
                              **probability_metrics(valid,valid[column])})
    pred=pd.concat(preds,ignore_index=True) if preds else pd.DataFrame()
    if len(pred):
        for label,column in [('BT','p_bt'),('BatchElo','p_elo'),('Half','p_half')]:
            summaries.append({'fold_start':'ALL_TEST','fold_end':'ALL_TEST','model':label,
                              **probability_metrics(pred,pred[column])})
    return pred,pd.DataFrame(summaries)
