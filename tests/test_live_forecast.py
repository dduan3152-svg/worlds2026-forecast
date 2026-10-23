"""Synthetic tournament fixtures only; no official results are created by tests."""
from pathlib import Path
from datetime import timedelta
import csv
import gzip
import io
import json
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from fetch_history import ALIASES
from live_forecast import (ROOT, load, write, date, utc, source_games, infer_results,
                           bootstrap, update, history_chain, export_site)

BASE=load(ROOT/'outputs/simulation_base.json')
TEAMS=BASE['teams']
NAMES={code:name for name,code in ALIASES.items()}
OPENING=[(r['a'],r['b']) for r in load(ROOT/'data/known_state.json')['first_round']]
EXAMPLE=BASE['examples'][0]
NOW=date('2026-11-16T00:00:00Z')


def fixture():
    maps=[];matches=[];clock=date('2026-10-24T00:00:00Z')
    for stage in EXAMPLE['stages']:
        if stage['stage']!='swiss':
            clock=max(clock,date({'quarterfinal':'2026-11-04T00:00:00Z','semifinal':'2026-11-08T00:00:00Z','final':'2026-11-14T20:00:00Z'}[stage['stage']]))
        for m in stage['matches']:
            matches.append({**m,'stage':stage['stage'],'round':stage['round'],'best_of':stage['best_of']})
            for number in range(1,stage['best_of']//2+2):
                clock+=timedelta(minutes=45)
                maps.append({'id':'fixture-'+str(len(maps)),'a':m['a'],'b':m['b'],'winner':m['winner'],
                             'number':number,'start':utc(clock),'end':utc(clock+timedelta(minutes=30))})
    return maps,matches


def gzip_maps(maps):
    fields=['game_id','date','league','team_name','opponent_team_name','side','result','gamelength','game_number']
    stream=io.StringIO();writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
    for m in maps:
        for a,b,side in [(m['a'],m['b'],'Blue'),(m['b'],m['a'],'Red')]:
            writer.writerow({'game_id':m['id'],'date':m['start'],'league':'WLDs','team_name':NAMES[a],
                'opponent_team_name':NAMES[b],'side':side,'result':'TRUE' if m['winner']==a else 'FALSE',
                'gamelength':1800,'game_number':m['number']})
    return gzip.compress(stream.getvalue().encode())


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        for directory in ['outputs','data']:
            shutil.copytree(ROOT/directory,self.root/directory)
        shutil.copyfile(ROOT/'config.json',self.root/'config.json')
        (self.root/'docs').mkdir();shutil.copyfile(ROOT/'docs/worlds2026_report.pdf',self.root/'docs/worlds2026_report.pdf')
        self.settings={'simulations':200,'announced_rounds':{},'quarterfinal_bracket':None,'draw_source_url':'','draw_as_of':None}
        write(self.root/'live/settings.json',self.settings)
        self.maps,self.matches=fixture()

    def tearDown(self):self.tmp.cleanup()

    def test_paired_rows_and_future_boundary(self):
        games,_=source_games(gzip_maps(self.maps[:1]),TEAMS,NOW)
        self.assertEqual(games,self.maps[:1])
        games,_=source_games(gzip_maps(self.maps[:1]),TEAMS,date(self.maps[0]['start']))
        self.assertEqual(games,[])
        raw=gzip.decompress(gzip_maps(self.maps[:1])).decode().splitlines()
        with self.assertRaises(ValueError):source_games(gzip.compress(('\n'.join(raw[:2])+'\n').encode()),TEAMS,NOW)

    def test_game_number_gap_rejected(self):
        bad=[dict(self.maps[0],number=2)]
        with self.assertRaises(ValueError):infer_results(bad,TEAMS,OPENING,self.settings)

    def test_partial_later_round_waits_for_draw(self):
        swiss,ko,pending=infer_results(self.maps[:9],TEAMS,OPENING,self.settings)
        self.assertEqual(len(swiss),8);self.assertTrue(pending);self.assertFalse(ko)
        round2=[(m['a'],m['b']) for m in self.matches if m['stage']=='swiss' and m['round']==2]
        swiss,_,_=infer_results(self.maps[:9],TEAMS,OPENING,{**self.settings,'announced_rounds':{'2':round2}})
        self.assertEqual(len(swiss),9)

    def test_complete_tournament_requires_bracket(self):
        swiss,ko,pending=infer_results(self.maps,TEAMS,OPENING,self.settings)
        self.assertEqual(len(swiss),33);self.assertEqual(ko,[]);self.assertTrue(pending)
        bracket=[x['teams'] for x in EXAMPLE['quarterfinal_bracket']]
        swiss,ko,pending=infer_results(self.maps,TEAMS,OPENING,{**self.settings,'quarterfinal_bracket':bracket})
        self.assertEqual(len(swiss),33);self.assertEqual(len(ko),7);self.assertFalse(pending)
        self.assertEqual(ko[-1]['winner'],EXAMPLE['champion'])

    def test_new_results_append_idempotently_and_corrections_stop(self):
        raw=gzip_maps(self.maps[:1]);self.assertTrue(update(self.root,raw,NOW))
        chain=history_chain(self.root);self.assertEqual(len(chain),2)
        hashes=[x['hash'] for x in chain]
        self.assertFalse(update(self.root,raw,NOW+timedelta(minutes=30)))
        self.assertEqual([x['hash'] for x in history_chain(self.root)],hashes)
        changed=dict(self.maps[0]);changed['winner']=changed['b'] if changed['winner']==changed['a'] else changed['a']
        with self.assertRaises(ValueError):update(self.root,gzip_maps([changed]),NOW)
        self.assertEqual([x['hash'] for x in history_chain(self.root)],hashes)
        self.assertEqual(load(self.root/'site/data.json')['history'][-1]['hash'],hashes[-1])

    def test_final_result_has_one_certain_champion(self):
        self.settings.update(quarterfinal_bracket=[x['teams'] for x in EXAMPLE['quarterfinal_bracket']],
                             draw_source_url='https://example.com/synthetic-fixture',draw_as_of='2026-11-01T00:00:00Z')
        write(self.root/'live/settings.json',self.settings)
        update(self.root,gzip_maps(self.maps),NOW)
        last=history_chain(self.root)[-1]
        self.assertEqual([r['team'] for r in last['rows'] if r['champion']==1],[EXAMPLE['champion']])
        self.assertEqual(sum(r['qualified'] for r in last['rows']),8)

    def test_tamper_is_detected(self):
        bootstrap(self.root);path=next((self.root/'live/history').glob('*.json'))
        snap=load(path);snap['rows'][0]['champion']=.99;write(path,snap)
        with self.assertRaises(ValueError):history_chain(self.root)

    def test_empty_source_never_fabricates_matches(self):
        self.assertFalse(update(self.root,gzip_maps([]),NOW))
        last=history_chain(self.root)[-1]
        self.assertEqual(last['kind'],'frozen_baseline');self.assertEqual(last['state']['swiss'],[])
        self.assertTrue(load(self.root/'live/status.json')['stale'])

if __name__=='__main__':unittest.main()
