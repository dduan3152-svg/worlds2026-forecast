"""Build the Chinese Worlds 2026 report from completed, frozen model outputs.

This script reads model data only. It creates docs/worlds2026_report.pdf and a
small report QA manifest. Run the analysis and figures scripts first; missing
inputs are an error, never silently replaced by invented or placeholder values.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import io
import json
import math
import re
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

import pandas as pd
from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, Frame, Image, KeepTogether, PageBreak, PageTemplate,
    Paragraph, Spacer, Table, TableStyle,
)

ROOT = Path(__file__).resolve().parents[1]
NAVY = colors.HexColor('#123047')
TEAL = colors.HexColor('#087F8C')
TEXT = colors.HexColor('#203447')
MUTED = colors.HexColor('#617184')
PALE = colors.HexColor('#F0F5F7')
GRID = colors.HexColor('#DCE5EA')
GOLD = colors.HexColor('#B67C26')
PAGE_W, PAGE_H = A4
MARGIN = 42
WIDTH = PAGE_W - 2 * MARGIN


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def pct(value, decimals=1):
    if value is None or pd.isna(value):
        return '-'
    value = float(value)
    if 0 < value < .001:
        return '<0.1%'
    return f'{100*value:.{decimals}f}%'


def nfmt(value):
    return f'{int(value):,}'


def clean_text(text):
    # Avoid nonbreaking/typographic hyphens in the final PDF.
    return str(text).replace('\u2011', '-').replace('\u2013', '-').replace('\u2014', '-')


class Report:
    def __init__(self, root):
        self.root = Path(root)
        self.out = self.root / 'outputs'
        self.fig = self.root / 'figures'
        required = [
            'data/teams.json', 'data/eliminated_teams.json', 'data/known_state.json',
            'data/rules.json', 'data/historical_lineups_vs_worlds_rosters.csv', 'config.json',
            'outputs/stage_probabilities.csv', 'outputs/bootstrap_summary.csv',
            'outputs/backtest_metrics.csv', 'outputs/calibration.csv',
            'outputs/international_backtest.json', 'outputs/data_audit.json',
            'outputs/fitted_model.json', 'outputs/sensitivity_summary.csv',
            'outputs/final_pairs.csv', 'outputs/representative_examples.json',
            'outputs/swiss_records.csv', 'outputs/round2_opponents_given_win.csv',
            'outputs/round2_opponents_given_loss.csv', 'outputs/round2_opponents_unconditional.csv', 'outputs/simulation_log.json',
            'outputs/bootstrap_log.json', 'outputs/matrix_bo1.csv',
            'outputs/matrix_bo3.csv', 'outputs/matrix_bo5.csv',
            'figures/assets/NotoSansSC-Regular.ttf',
            'figures/01_matchup_matrix_bo1.png',
            'figures/02_champion_probabilities.png',
            'figures/03_calibration.png', 'figures/04_swiss_records.png',
        ]
        missing = [x for x in required if not (self.root / x).exists()]
        if missing:
            raise FileNotFoundError('Report inputs not ready:\n' + '\n'.join(missing))
        self.required = required
        self.teams = read_json(self.root / 'data/teams.json')
        self.eliminated = read_json(self.root / 'data/eliminated_teams.json')
        self.allteams = {t['code']: t for t in self.teams + self.eliminated}
        self.state = read_json(self.root / 'data/known_state.json')
        self.rules = read_json(self.root / 'data/rules.json')
        self.config = read_json(self.root / 'config.json')
        self.audit = read_json(self.out / 'data_audit.json')
        self.model = read_json(self.out / 'fitted_model.json')
        self.sim = read_json(self.out / 'simulation_log.json')
        self.bootlog = read_json(self.out / 'bootstrap_log.json')
        self.stage = pd.read_csv(self.out / 'stage_probabilities.csv').set_index('team')
        self.stage = self.stage.sort_values('champion', ascending=False)
        self.boot = pd.read_csv(self.out / 'bootstrap_summary.csv').set_index('team')
        self.metrics = pd.read_csv(self.out / 'backtest_metrics.csv')
        self.calibration = pd.read_csv(self.out / 'calibration.csv')
        self.international = read_json(self.out / 'international_backtest.json')
        self.sensitivity = pd.read_csv(self.out / 'sensitivity_summary.csv')
        self.finalpairs = pd.read_csv(self.out / 'final_pairs.csv')
        self.records = pd.read_csv(self.out / 'swiss_records.csv').set_index('team')
        self.r2win = pd.read_csv(self.out / 'round2_opponents_given_win.csv').set_index('team')
        self.r2loss = pd.read_csv(self.out / 'round2_opponents_given_loss.csv').set_index('team')
        self.r2all = pd.read_csv(self.out / 'round2_opponents_unconditional.csv').set_index('team')
        self.matrices = {bo: pd.read_csv(self.out / f'matrix_bo{bo}.csv', index_col=0) for bo in (1, 3, 5)}
        self.examples = read_json(self.out / 'representative_examples.json')
        self.lineups = pd.read_csv(self.root / 'data/historical_lineups_vs_worlds_rosters.csv').set_index('team')
        self.leader = self.stage.index[0]
        self.active = [c for c in self.stage.index if c in {t['code'] for t in self.teams}]
        assert len(self.stage) == 19 and len(self.active) == 16
        assert set(self.stage.index) == set(self.allteams)
        assert abs(self.stage.champion.sum() - 1) < 1e-8
        assert abs(self.stage.qualified.sum() - 8) < 1e-8
        assert (self.stage.loc[[t['code'] for t in self.eliminated], ['qualified', 'semifinalist', 'finalist', 'champion']] == 0).all().all()
        assert len(self.state['first_round']) == 8
        assert all(not m['completed'] for m in self.state['first_round'])
        assert self.calibration.bin_low.min() >= .5, 'Use the favorite-perspective calibration table.'
        self.styles = self.make_styles()
        self.font_chars = set(pdfmetrics.getFont('NotoSC').face.charToGlyph)
        self.story = []

    def make_styles(self):
        fontfile = self.fig / 'assets/NotoSansSC-Regular.ttf'
        if 'NotoSC' not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont('NotoSC', str(fontfile)))
            pdfmetrics.registerFontFamily('NotoSC', normal='NotoSC', bold='NotoSC', italic='NotoSC', boldItalic='NotoSC')
        base = dict(fontName='NotoSC', textColor=TEXT, wordWrap='CJK', splitLongWords=True)
        return {
            'title': ParagraphStyle('title', fontSize=27, leading=36, spaceAfter=9, **base),
            'h1': ParagraphStyle('h1', fontSize=20, leading=28, spaceAfter=12, **base),
            'h2': ParagraphStyle('h2', fontSize=12, leading=18, spaceBefore=10, spaceAfter=5, **base),
            'body': ParagraphStyle('body', fontSize=10, leading=16.5, spaceAfter=9, **base),
            'small': ParagraphStyle('small', fontSize=8.4, leading=13, spaceAfter=6, **base),
            'tiny': ParagraphStyle('tiny', fontSize=7.5, leading=11, spaceAfter=4, **base),
            'caption': ParagraphStyle('caption', fontSize=8.2, leading=12, textColor=MUTED, fontName='NotoSC', wordWrap='CJK', spaceAfter=7),
            'kicker': ParagraphStyle('kicker', fontSize=8.6, leading=13, textColor=TEAL, fontName='NotoSC', spaceAfter=5),
            'cell': ParagraphStyle('cell', fontSize=8.3, leading=12.6, **base),
            'cellsmall': ParagraphStyle('cellsmall', fontSize=7.5, leading=11.4, **base),
            'headcell': ParagraphStyle('headcell', fontSize=8.2, leading=12, textColor=colors.white, fontName='NotoSC', wordWrap='CJK'),
            'number': ParagraphStyle('number', fontSize=24, leading=32, textColor=TEAL, fontName='NotoSC'),
        }

    def p(self, text, kind='body'):
        text = clean_text(text)
        visible = html.unescape(re.sub(r'<[^>]+>', '', text))
        absent = sorted({char for char in visible if ord(char) >= 32 and ord(char) not in self.font_chars})
        if absent:
            raise ValueError('Report font lacks glyphs: ' + repr(absent))
        return Paragraph(text, self.styles[kind])

    def add(self, text, kind='body'):
        self.story.append(self.p(text, kind))

    def section(self, number, title, category):
        if self.story:
            self.story.append(PageBreak())
        self.add(f'{number:02d} / {category}', 'kicker')
        self.add(title, 'h1')

    def table(self, headers, rows, widths=None, small=False, highlights=()):
        widths = widths or [WIDTH/len(headers)] * len(headers)
        style = 'cellsmall' if small else 'cell'
        content = [[self.p(escape(str(x)), 'headcell') for x in headers]]
        content.extend([[self.p(escape(clean_text(x)).replace('\n', '<br/>'), style) for x in row] for row in rows])
        t = Table(content, colWidths=widths, repeatRows=1, hAlign='LEFT')
        ts = [
            ('BACKGROUND', (0, 0), (-1, 0), NAVY),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 7),
            ('RIGHTPADDING', (0, 0), (-1, -1), 7),
            ('TOPPADDING', (0, 0), (-1, 0), 8),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('TOPPADDING', (0, 1), (-1, -1), 6 if not small else 5),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 6 if not small else 5),
            ('LINEBELOW', (0, 0), (-1, 0), .5, GRID),
            ('LINEBELOW', (0, 1), (-1, -1), .35, GRID),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, PALE]),
        ]
        for row in highlights:
            ts.append(('BACKGROUND', (0, row + 1), (-1, row + 1), colors.HexColor('#DDF0EF')))
        t.setStyle(TableStyle(ts))
        self.story.extend([t, Spacer(1, 9)])

    def box(self, title, text, shade=PALE):
        t = Table([[self.p(title, 'h2')], [self.p(text, 'body')]], colWidths=[WIDTH-20])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), shade),
            ('BOX', (0, 0), (-1, -1), .5, GRID),
            ('LEFTPADDING', (0, 0), (-1, -1), 12),
            ('RIGHTPADDING', (0, 0), (-1, -1), 12),
            ('TOPPADDING', (0, 0), (-1, 0), 8),
            ('BOTTOMPADDING', (0, -1), (-1, -1), 9),
        ]))
        self.story.extend([t, Spacer(1, 9)])

    def figure(self, name, max_height, max_width=WIDTH):
        path = self.fig / name
        image_data=path.read_bytes()
        with PILImage.open(io.BytesIO(image_data)) as im:
            im.load()
            w, h = im.size
        scale = min(max_width/w, max_height/h)
        self.story.extend([Image(io.BytesIO(image_data), width=w*scale, height=h*scale, hAlign='CENTER'), Spacer(1, 5)])

    def interval(self, team, key='champion'):
        if team not in self.active:
            return '已淘汰 / 固定0'
        return f"{pct(self.boot.loc[team, key+'_q05'])} - {pct(self.boot.loc[team, key+'_q95'])}"

    def render_content(self):
        lead = self.leader
        lp = self.stage.loc[lead]
        top4 = self.active[:4]
        # 1. An actionable answer before the machinery.
        self.add('WORLDS 2026 / S16 / 冻结时点预测', 'kicker')
        self.add('世界赛赛程与夺冠预测', 'title')
        self.add('北京时间 2026年10月23日 15:25:50<br/>版本 1.0 / 首轮已抽签、瑞士轮尚未开赛', 'caption')
        self.box(f'冠军首选：{lead} / {pct(lp.champion)}',
                 f'当前模型最看好 {lead}，其次是 ' + '、'.join(f'{t}（{pct(self.stage.loc[t,"champion"])}）' for t in top4[1:]) +
                 f'。{lead} 的夺冠点估计约为三分之一，其他队伍合计仍有 {pct(1-lp.champion)} 的夺冠概率。')
        self.add(f'模型输出 / {lead} 的夺冠90%系列赛重采样区间为 {self.interval(lead)}。区间反映已选模型内的数据扰动；版本、临场策略和未记录阵容变动仍可能扩大误差。', 'small')
        self.add('已核实赛果 / GEN在9月5日、13日均以3-1击败HLE；HLE是本届MSI冠军。LPL方面，AL在9月13日决赛3-1击败BLG。模型综合全年结果并衰减旧比赛，不把赛区种子或单场胜负直接等同于全球排名。[S3]', 'small')
        self.figure('02_champion_probabilities.png', 345)
        self.add('阅读顺序：第2页查看19队阶段概率；第3页查看真实首轮；第4-6页查看后续赛程分布；第9-12页核对数据、回测和敏感性。', 'small')
        self.add('数据与判断分层：赛程、名单和已结束比赛标为“已核实”；概率标为“模型输出”；未从数据估计的改变标为“情景假设”。本报告的概率不是赔率。', 'caption')

        # 2. Full participant coverage, with eliminated teams conditioned to zero.
        self.section(2, '19队完整概率表', '模型输出 / 已知淘汰状态')
        self.add('共有19队参加本届世界赛；截至冻结时点，16队进入瑞士轮，C9、FUR、MVK已在入围赛淘汰。后三队的未来阶段概率固定为0。[S1][S3]', 'small')
        rows=[]
        for team, r in self.stage.iterrows():
            meta = self.allteams[team]
            name = meta['name']
            rows.append([f'{team} / {name}', f"{meta['region']} {meta['seed']}",
                         pct(r.qualified), pct(r.semifinalist), pct(r.finalist), pct(r.champion), self.interval(team)])
        self.table(['队伍', '种子', '八强', '四强', '决赛', '冠军', '冠军90%重采样区间'], rows,
                   [140, 43, 50, 50, 50, 50, WIDTH-383], small=True, highlights=(0,))
        self.add('八强 = 瑞士轮取得3胜。四强、决赛、冠军概率均从当前时点无条件计算；不同队伍的八强概率总和为800%，冠军概率总和为100%。', 'small')
        self.add('区间由200次完整系列赛权重重采样得到，不具有经验证的90%覆盖保证，也不包含全部模型风险。表格一般保留1位小数；CSV保留完整精度；非零但小于0.1%的估计显示为“<0.1%”。', 'caption')

        # 3. Actual known first round and actual calendar.
        self.section(3, '首轮：真实对阵与单局预测', '已核实赛程 + 模型输出')
        rows=[]; close=[]
        for m in self.state['first_round']:
            a,b=m['a'],m['b']; prob=float(self.matrices[1].loc[a,b])
            hour=datetime.fromisoformat(m['start_bjt']).strftime('%H:%M')
            if m.get('start_time_is_approximate'):hour='约'+hour
            winner=a if prob>=.5 else b
            rows.append([hour, f'{a} - {b}', pct(prob), pct(1-prob), winner])
            close.append((abs(prob-.5),a,b,prob))
        self.add('以下全部为北京时间10月24日，比赛形式均为BO1。第一场01:00开赛；后续时间随前场比赛进度调整。[S1][S3][S9]', 'small')
        self.table(['时间', '已公布对阵', '左队胜率', '右队胜率', '倾向'], rows, [75,150,90,90,WIDTH-405])
        closest=sorted(close)[:2]
        self.add('最接近五五开的两场：' + '；'.join(f'{a} - {b}，左队胜率{pct(prob)}' for _,a,b,prob in closest) + '。这种小幅倾向很容易被阵容、BP和单局波动改变。', 'small')
        self.add('接下来何时比赛？', 'h2')
        self.table(['阶段', '北京时间'], [
            ['瑞士轮', '10月24-27日、11月1日 01:00；10月29-31日 04:00'],
            ['八强', '11月4-7日，每日06:00'],
            ['半决赛', '11月8-9日，每日06:00'],
            ['决赛', '11月15日03:00'],
        ], [80,WIDTH-80])
        self.add('第2轮及之后的具体对手，须结合前轮赛果和现场抽签产生。官方日程以美国当地日期发布，本表已换算北京时间。[S1]', 'caption')

        # 4. Predict distributions rather than make up a fixed Swiss bracket.
        self.section(4, '瑞士轮：16队的六种终局战绩', '模型输出 / 非官方后续签表')
        self.figure('04_swiss_records.png', 410)
        qualified = self.stage.loc[self.active].sort_values('qualified',ascending=False)
        forecast8 = qualified.head(8).index.tolist()
        self.add('按各队边际晋级概率排序的前八：'+'、'.join(forecast8)+'。这是八个独立边际概率的排序，不能把它解释为发生概率最高的完整八强组合。', 'small')
        self.add(f'{lead}以3-0晋级的概率为{pct(self.records.loc[lead,"3-0"])}；但3-0不是夺冠的必要条件。基准模拟中每次恰有2支3-0、3支3-1和3支3-2队伍晋级，三种晋级记录合计八强概率。', 'small')
        self.add('瑞士轮赛制为三胜晋级、三负淘汰；晋级或淘汰的关键场为BO3，其余为BO1。战绩图显示各队所有六种终局，完整16×6数表另见swiss_records.csv。[S1][S2]', 'caption')

        self.section(5, '第二轮：16队可能遇到谁', '模型输出 / 无条件边际对手概率')
        self.add('在首轮尚未进行的当前时点，综合所有首轮胜负和合法抽签，每队第二轮对手概率最高的三项如下。每行只列前三，其余概率分散于其他对手；不是三选一签表。', 'small')
        rows=[]
        for team in self.active:
            vals=self.r2all.loc[team].sort_values(ascending=False).head(3)
            rows.append([team]+[f'{opp} / {pct(value)}' for opp,value in vals.items()])
        self.table(['队伍','可能对手1','可能对手2','可能对手3'],rows,[55,(WIDTH-55)/3,(WIDTH-55)/3,(WIDTH-55)/3],small=True)
        self.add('条件改变，对手分布也会改变', 'h2')
        rows=[]
        for label,frame in [(f'{lead}首轮获胜',self.r2win),(f'{lead}首轮失利',self.r2loss)]:
            vals=frame.loc[lead].sort_values(ascending=False).head(3)
            rows.append([label,'；'.join(f'{opp} {pct(value)}' for opp,value in vals.items())])
        self.table(['条件','该条件下第二轮对手前三'],rows,[100,WIDTH-100],small=True)
        self.add('只固定该队首轮结果，其他比赛仍被模拟。第二轮按同战绩分组，避免瑞士轮重赛，可同赛区相遇。完整无条件与两种条件16×16矩阵随文件包保存。[S1][S2]', 'caption')

        # 5. Finals distribution and one explicitly illustrative actual simulation.
        self.section(6, '淘汰赛：决赛组合与一条示例路径', '模型输出 / 随机实现示例')
        fp=self.finalpairs.sort_values('probability',ascending=False).head(5)
        self.table(['可能的决赛双方','出现概率'], [[f'{r.team_a} - {r.team_b}',pct(r.probability)] for r in fp.itertuples()], [WIDTH-110,110])
        self.add('即便最常出现的决赛组合，也只占全部模拟中的一小部分。八强抽签会把两支3-0队伍分别放在上下半区；余下对阵仍有大量变化。[S2]', 'small')
        examples=[x.get('path',x) for x in self.examples]
        example=next((x for x in examples if x.get('champion')==lead),examples[0])
        self.add(f'示例：一次由 {example["champion"]} 夺冠的完整随机实现', 'h2')
        self.add(f'以下取自实际保存的模拟编号 {example.get("simulation_index","-")}。它是展示规则如何展开的一个情景，不是官方签表，也不是声称概率最高的唯一剧情。', 'small')
        phase={'quarterfinal':'八强','semifinal':'半决赛','final':'决赛'};rows=[];leaderpath=[]
        for stage in example['stages']:
            for match in stage['matches']:
                if stage['stage']=='swiss' and lead in (match['a'],match['b']):
                    opp=match['b'] if match['a']==lead else match['a']
                    leaderpath.append(f'R{stage["round"]} 对{opp} '+('胜' if match['winner']==lead else '负'))
                elif stage['stage'] in phase:
                    rows.append([phase[stage['stage']],f'{match["a"]} - {match["b"]}',match['winner']])
        self.add(f'{lead}的瑞士轮示例：'+'；'.join(leaderpath)+'。', 'small')
        self.table(['阶段','该次模拟对阵','该次模拟胜者'], rows,[75,WIDTH-155,80],small=True)
        self.add('示例保存了每轮的真实模拟胜者，但不生成未经模拟的3-1、3-2等比分。后续若有新赛果，应把已发生结果作为条件重新计算。', 'caption')

        # 6. Matchup matrix with explicit BO conversion assumptions.
        self.section(7, '对阵强弱：单局与系列赛', '模型输出 / 当前16队')
        self.figure('01_matchup_matrix_bo1.png', 470)
        rows=[]
        for opponent in top4[1:]:
            rows.append([f'{lead} 胜 {opponent}']+[pct(self.matrices[bo].loc[lead,opponent]) for bo in (1,3,5)])
        self.table(['假定交手','BO1','BO3','BO5'], rows,[WIDTH-225,75,75,75],small=True)
        self.add('BO3/BO5基准值假定每局胜率固定且条件独立；优势会随系列赛长度放大。共享状态、局间应对和选边政策可能削弱这种放大，第12页另做局间相关性压力情景。', 'small')
        self.add('附带CSV包含全部19队的假想BO1/BO3/BO5矩阵。已淘汰三队在这些矩阵仍可有假想交手胜率，但本届剩余赛程中的晋级、夺冠概率已经为0。', 'caption')

        # 7. Full starting-five inventory, explicitly not an official substitute list.
        self.section(8, '19队名单与报道中的首发', '已核实报道 / 不等于完整注册大名单')
        self.add('位置顺序为上单、打野、中单、ADC、辅助。首发来源为赛前队伍报道；不补造未核实的替补或比赛当天提交名单。[S5][S6]', 'small')
        order=[t['code'] for t in self.teams]+[t['code'] for t in self.eliminated]
        rows=[]
        for code in order:
            team=self.allteams[code];byrole={x['role']:x['player'] for x in team['roster']}
            rows.append([code+(' *' if code in {t['code'] for t in self.eliminated} else ''),f'{team["region"]}{team["seed"]}']+[byrole.get(r,'未核实') for r in ['TOP','JUNGLE','MID','BOT','SUPPORT']])
        self.table(['队伍','种子','上单','打野','中单','ADC','辅助'],rows,[44,44,83,80,85,88,WIDTH-424],small=True)
        self.add('* C9、FUR、MVK已淘汰。TES因ZUIAN签证问题宣布由369加入首发；10月22日赛前采访再次确认369归队准备比赛。[S7][S8]', 'small')
        self.add('阵容核实用于解释预测边界。基准模型未使用逐局历史首发特征，未把“369回归”“Peyz加盟”等转化成经数据估计的单独选手加减分。TES正负调整仅是明确标注的压力情景。', 'caption')
        coverage='；'.join(f'{code} {int(self.lineups.loc[code,"primary_games_with_reported_five"])}/{int(self.lineups.loc[code,"primary_games"])}局' for code in ['AL','IG','TES'])
        self.add(f'与本届报道五人组合完全一致的历史覆盖：{coverage}。369此前与其他队友的比赛不能等同于本届TES组合。队伍级参数混合了旧阵容，换人影响尚未估计。[S11]', 'caption')

        # 8. Data coverage and provenance.
        self.section(9, '数据：哪些进入模型，哪些没有', '可追溯证据 / 数据审核')
        a=self.audit
        self.table(['项目','本次实际输入'],[
            ['时间范围', '2026年1月14日至10月18日已结束系列赛'],
            ['观测结果',f'{nfmt(a["total_map_outcomes"])}个单局结果计数 = {nfmt(a["actual_map_rows"])}条真实逐局记录 + {a["residual_count_rows"]}条官方比分残余计数'],
            ['历史覆盖',f'{nfmt(a["series"])}个系列赛，{a["teams"]}支历史队伍'],
            ['跨赛区连接',f'{a["known_cross_region_maps"]}局 / {a["known_cross_region_series"]}个系列赛'],
            ['首发与字段限制','真实逐局首发已有辅助核对表；基准模型未估计阵容项；2个残余计数无选边与版本'],
            ['主模型赛事范围','六大赛区、FST、MSI、EWC正赛、Americas Cup、LPL资格赛、世界赛入围赛'],
        ],[110,WIDTH-110])
        self.add('如何避免把缺数据写成假比赛', 'h2')
        self.add('历史逐局来源为ChainCC公开数据快照；Riot官方赛程API累计对照1,004个系列赛，其中1,002场比分完全一致，2场各缺少1局胜负计数。对账范围包含扩展赛程，与主样本的1,005场并非同一集合。补充仅保留官方比分能证明的残余结果，不发明时间、选边或逐局顺序。[S3][S4]', 'body')
        self.add('独立抽查14个关键锚点，原始局数、比分、队名别名和清洗输出全部吻合。原始数据的WLDs标签包含12局LPL资格赛和22局真正的世界赛入围赛，清洗已分开，不能把34局全部当作世界赛。', 'small')
        self.add('主模型排除EWC资格赛和DCGI；另设扩展赛事情景。比赛记录的蓝红方是已知历史信息，未来选边则按未知处理。源文件的“16.20”等版本标签保留原样，没有暗中改写成官方赛事版本“26.20”。', 'small')
        self.box('冻结范围', '信息截止北京时间10月23日15:25:50。首场瑞士轮在10月24日01:00开始，冻结时刻没有任何瑞士轮结果。报告使用冻结数据做按事件时间切分的历史回测；并非每个过去预测时点都保存了当时版本的数据档案。')

        # 9. Model derivation and reproducibility, with the assumptions visible.
        self.section(10, '方法：从已结束比赛到赛事概率', '建模方法 / 明确假设')
        self.add('1. 用结果估计队伍强度', 'h2')
        self.add(f'采用带时间衰减和正则化的Bradley-Terry模型。每支队伍估计一个强度参数，同时拟合历史蓝方系数。旧比赛权重每 {self.model["half_life"]:g} 天减半；正则化强度 {self.model["ridge"]:g} 防止小样本极端化。', 'body')
        self.add('单局概率 = 对未来蓝/红方各50%加权的 sigmoid((队伍A强度 - 队伍B强度 ± 蓝方系数) / 温度)。', 'small')
        self.add(f'历史蓝方系数为 {self.model["side_beta"]:.3f}，温度为 {self.model["temperature"]:g}。这是关联性拟合，不是蓝方选择的因果收益；First Selection的选择政策没有单独估计。[S2]', 'small')
        self.add('2. 先调参，再留出回测', 'h2')
        self.table(['用途','时间','每段如何预测'],[
            ['调参','5月、6月、7月','只用该月开始前已完成系列赛；选择半衰期、正则与温度'],
            ['留出测试','8月、9月、10月截至冻结','参数固定；BT与Elo均在每段起点训练并冻结'],
            ['正式预测','截至10月23日冻结时刻','用完整可用历史重新拟合，再模拟未来赛程'],
        ],[65,135,WIDTH-200],small=True)
        self.add('边界处跨期的整段系列赛剔除；官方残余计数与已有逐局归并为同一系列赛，避免在同一系列赛内部先看到结果再作预测。', 'small')
        self.add('3. 把单局概率传入赛制', 'h2')
        self.add('基准BO3胜率为 3p<super>2</super> - 2p<super>3</super>；BO5为 10p<super>3</super> - 15p<super>4</super> + 6p<super>5</super>。每次赛事同时模拟胜负和合法抽签，保留已公布首轮、同战绩匹配、避免重赛及八强分区。', 'body')
        self.add('抽签采用“按抽球顺序放入最早能完成合法签表的位置”的实现，并另测均匀合法配对。该实现对应公开规则文字，但不声称复刻了Riot全部内部操作细节。[S2]', 'small')
        self.add(f'实际运行：基准 {nfmt(self.sim["baseline_completed"])} 次；含基准共 {len(self.sensitivity)} 个情景，每个100,000次；另进行200次系列赛权重重采样、每次10,000场赛事。随机种子分别为{self.config["simulation_seed"]}与{self.config["bootstrap_seed"]}。', 'caption')

        # 10. Honest out-of-time validation and the small international sample.
        self.section(11, '回测：有提升，但跨区验证仍有限', '留出测试 / 不是冠军概率校准证明')
        overall=self.metrics[self.metrics.fold_start=='ALL_TEST']
        modelnames={'BT':'本模型BT','BatchElo':'基础Elo','Half':'恒定50%'}
        self.table(['方法','单局准确率','Brier','对数损失'],[
            [modelnames.get(r.model,r.model),pct(r.accuracy),f'{r.brier:.4f}',f'{r.log_loss:.4f}'] for r in overall.itertuples()
        ],[WIDTH-240,90,75,75],small=True)
        bt=overall[overall.model=='BT'].iloc[0]
        self.add(f'8月至冻结时刻共 {int(bt.map_outcomes)}局、{int(bt.series)}个系列赛。Brier与对数损失越低越好。10月切片仅38局，BT在该切片没有优于Elo，不能把整体优势描述成每段都领先。', 'small')
        intl=next(x for x in self.international if x['model']=='BT')
        self.box('真正接近世界赛环境的留出样本很少',f'跨赛区留出仅 {intl["map_outcomes"]}局、{intl["series"]}个系列赛；BT的Brier为 {intl["brier"]:.3f}，接近50%基线的0.250。因此不能声称模型已对世界赛或冠军概率实现精确校准。')
        self.figure('03_calibration.png', 335)
        self.add('校准图按“模型看好的一方”分成50%-100%的5档，显示预测均值、实测胜率与样本量。80%-90%档只有10局；90%-100%档没有观测。系列赛内各局相关，不能当作711个独立样本。', 'caption')

        # 11. Sensitivity and resampling intervals must not be conflated with MC error.
        self.section(12, '敏感性：首选稳定，概率幅度不稳定', '预设情景 / 参数重采样')
        names={
            'baseline':'基准模型', 'half_life_30':'半衰期30天', 'half_life_365':'半衰期365天',
            'international_half_weight':'跨区结果权重减半', 'international_double_weight':'跨区结果权重翻倍',
            'uniform_legal_draw':'均匀合法抽签', 'exclude_EWC':'排除EWC正赛',
            'extended_events':'纳入EWC资格赛与DCGI', 'lpl_strength_plus_0p30':'假设LPL强度 +0.30',
            'lck_strength_plus_0p30':'假设LCK强度 +0.30', 'tes_roster_minus_0p30':'假设TES强度 -0.30',
            'tes_roster_plus_0p30':'假设TES强度 +0.30', 'series_correlation_0.10':'假设局间相关系数0.10',
            'series_correlation_0.25':'假设局间相关系数0.25',
            'lineup_transfer_0.50':'旧阵容每队权重 ×0.50',
            'lineup_transfer_0.25':'旧阵容每队权重 ×0.25',
        }
        self.table(['情景','冠军首选','首选的夺冠概率'],[[names.get(r.scenario,r.scenario),r.leader,pct(r.leader_champion_probability)] for r in self.sensitivity.itertuples()], [WIDTH-195,80,115],small=True)
        if any(self.sensitivity.scenario.str.startswith('lineup_transfer_')):
            self.add('新增阵容情景：历史五人组合与冻结时点基准不同，每队权重乘0.50或0.25，双方不同则相乘。世界赛队伍以报道名单为基准，其他队伍用截止前最近观测阵容。这是未回测、非因果的压力检验；当前名单未反填到历史回测特征中。', 'caption')
        self.add('解释三种不同的不确定性', 'h2')
        self.add(f'<b>有限模拟误差：</b>{lead}基准夺冠概率的蒙特卡洛标准误约 {lp.champion_mc_se*100:.2f} 个百分点；这只衡量模拟抽样精度。增加模拟次数可以缩小它，不能修正错误假设。', 'small')
        self.add(f'<b>模型内数据扰动：</b>{lead}的90%重采样区间为{self.interval(lead)}。200次重拟合中，{lead}有 {round(self.boot.loc[lead,"bootstrap_leader_fraction"]*200)} 次排在冠军概率首位（{pct(self.boot.loc[lead,"bootstrap_leader_fraction"])}）；这个比例不是它的夺冠概率。', 'small')
        leader_count=int((self.sensitivity.leader==lead).sum())
        self.add(f'<b>结构与情景风险：</b>赛区漂移、TES加减分及局间相关系数是压力假设，没有由选手或版本数据估计。在{len(self.sensitivity)}个预设情景中，{lead}有{leader_count}个情景排名第一；未纳入情景仍可能改变排序。', 'caption')

        # 12. Limits, sources, and deliverable routing.
        self.section(13, '使用边界与可复核来源', '解释范围 / 证据目录')
        self.add('如何使用这份预测', 'h2')
        self.add(f'可以用它比较当前队伍的相对位置、首轮倾向和晋级路线的不确定性。新比赛、已确认阵容变更或新抽签出现后，应更新已知状态并重跑。当前的{pct(lp.champion)}不应在赛程推进后原封不动沿用。', 'small')
        self.add('最主要的边界是：跨赛区验证很少；尚未估计阵容变更的战力影响；没有队伍特定的26.20版本适应系数；基准系列赛假定条件独立；抽签实现只对应公开文字。已取得的国际规则含Fearless误选处理，但未完整给出禁用继承定义；本报告不声称验证其全部操作细节，也未估计英雄池收益。[S10]', 'small')
        self.add('来源索引', 'h2')
        sources=[
            ('S1','Riot / Worlds 2026 Primer：参赛队、抽签档位、赛制及日程','https://lolesports.com/en-GB/news/worlds-2026-primer'),
            ('S2','Riot / Worlds 2026 Event Specific Ruleset v1.01：4.1、4.3及5.1','https://cdn.sanity.io/files/dsfx7636/news_live/faa5ce974e58615911fbee931c6123e2785a8b46.pdf'),
            ('S3','Riot / LoL Esports赛程：官方比分；API快照及hash随项目保存','https://lolesports.com/zh-TW/'),
            ('S4','ChainCC / 2026队伍逐局公开数据：本次原始输入快照','https://chaincc.lol/data/chaincc-teams-2026.csv.gz'),
            ('S5','GosuGamers / Worlds 2026队伍与选手名单','https://www.gosugamers.net/lol/news/79314-all-teams-and-players-qualified-for-the-league-of-legends-world-championship-2026'),
            ('S6','TipsGG / 10月15日19队报道首发','https://tips.gg/article/lol-worlds-2026-teams-rosters/'),
            ('S7','TES公告转述 / 10月6日369替换与签证说明','https://news.zhibo8.com/game/2026-10-06/6ac484c493fe3native.htm'),
            ('S8','10月22日JackeyLove赛前采访 / 369归队准备','https://news.zhibo8.com/game/2026-10-22/6ad96ac33fb35native.htm'),
            ('S9','CNMO报道公布的首轮具体顺序与时间','https://feng.ifeng.com/c/8xN9PJ6IRc5'),
            ('S10','Riot / 2026国际赛事竞赛规则v1.1：含Fearless误选处理','https://cdn.sanity.io/files/dsfx7636/news_live/d29e446e0a0d0813a8caaae1d2817dd82cfe79c6.pdf'),
            ('S11','ChainCC / 2026逐局选手记录：历史首发辅助核对，不作冻结模型特征','https://chaincc.lol/data/chaincc-players-2026.csv.gz'),
        ]
        for key,title,url in sources:
            self.add(f'[{key}] {escape(title)}<br/><link href="{escape(url)}" color="#087F8C">{escape(url)}</link>', 'tiny')
        self.add('复核路径', 'h2')
        self.add('原始与清洗记录在data/；源快照、hash和独立QA在evidence/；完整矩阵、阶段概率、重采样、敏感性与示例路径在outputs/；模型及重现脚本在src/。PDF由build_report.py读取这些输出生成，不手工覆盖概率。', 'tiny')

    def build(self, destination):
        self.render_content()
        destination=Path(destination)
        destination.parent.mkdir(parents=True,exist_ok=True)
        doc=BaseDocTemplate(str(destination),pagesize=A4,leftMargin=MARGIN,rightMargin=MARGIN,
                            topMargin=45,bottomMargin=43,title='S16世界赛赛程与夺冠预测 - 2026年10月23日冻结版',
                            author='Worlds 2026 analysis',subject='Frozen-data probabilistic forecast with validation and source audit')
        frame=Frame(MARGIN,43,WIDTH,PAGE_H-88,id='main',leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0)
        def decorate(canvas,document):
            canvas.saveState()
            canvas.setStrokeColor(GRID);canvas.setLineWidth(.5)
            canvas.line(MARGIN,31,PAGE_W-MARGIN,31)
            canvas.setFont('NotoSC',7);canvas.setFillColor(MUTED)
            canvas.drawString(MARGIN,19,'WORLDS 2026 / 截止北京时间 2026.10.23 15:25:50')
            canvas.drawRightString(PAGE_W-MARGIN,19,f'{document.page:02d}')
            if document.page>1:
                canvas.setFont('NotoSC',7.2)
                canvas.drawString(MARGIN,PAGE_H-24,'S16 世界赛 / 赛程与夺冠预测')
                canvas.drawRightString(PAGE_W-MARGIN,PAGE_H-24,'事实、输出、假设分开标注')
            canvas.restoreState()
        doc.addPageTemplates(PageTemplate(id='report',frames=[frame],onPage=decorate))
        doc.build(self.story)
        from pypdf import PdfReader
        reader=PdfReader(destination)
        texts=[page.extract_text() or '' for page in reader.pages]
        if not 12<=len(reader.pages)<=16:
            raise AssertionError(f'Unexpected report length: {len(reader.pages)} pages; inspect layout.')
        joined='\n'.join(texts)
        for required in ['19队完整概率表','首轮','回测','敏感性','来源索引','GEN','C9','MVK','FUR']:
            assert required in joined, f'Missing PDF text: {required}'
        if '\ufffd' in joined:
            raise AssertionError('Unicode replacement character found in extracted PDF text')
        manifest={
            'output':str(destination.relative_to(self.root)),
            'sha256':hashlib.sha256(destination.read_bytes()).hexdigest(),
            'pages':len(reader.pages),'page_text_characters':[len(t) for t in texts],
            'information_cutoff_utc':self.config['cutoff_utc'],
            'input_hashes':{name:hashlib.sha256((self.root/name).read_bytes()).hexdigest() for name in self.required},
            'text_validation':'passed; visual PNG inspection must be completed separately',
        }
        (destination.parent/'report_build_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({k:v for k,v in manifest.items() if k!='input_hashes'},ensure_ascii=False,indent=2))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    report=Report(args.root.resolve())
    report.build(args.output or args.root.resolve()/'docs/worlds2026_report.pdf')


if __name__=='__main__':
    main()
