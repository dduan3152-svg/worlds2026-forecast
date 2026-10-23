'use strict';
const $=s=>document.querySelector(s), labels={qualified:'八强',semifinalist:'四强',finalist:'决赛',champion:'夺冠'};
let data, selected=0, stage='champion';
const percent=p=>(100*p).toFixed(1), fmt=t=>t?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(t)):'尚未检查';
const displayPercent=p=>p>0&&p<.001?'<0.1':percent(p);
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n};
function option(value,text){const n=el('option','',text);n.value=value;return n}
async function fetchData(){
 $('#refresh').disabled=true;
 try{const response=await fetch('data.json',{cache:'no-store'});if(!response.ok)throw Error('无法读取预测数据');const incoming=await response.json();if(incoming.schema!==1||!incoming.history?.length)throw Error('预测数据格式无效');
 const oldId=data?.history[selected]?.id,following=!data||selected===data.history.length-1;data=incoming;
 selected=following?data.history.length-1:Math.max(0,data.history.findIndex(s=>s.id===oldId));
 $('#version').replaceChildren(...data.history.map((s,i)=>option(i,fmt(s.cutoff)+(i===0?' · 赛前冻结':' · 赛果更新'))));
 const regions=[...new Set(data.history[0].rows.map(r=>r.region))].sort();const oldRegion=$('#region').value;
 $('#region').replaceChildren(option('','全部赛区'),...regions.map(r=>option(r,r)));$('#region').value=oldRegion;
 for(const id of ['teamA','teamB']){const old=$('#'+id).value;$('#'+id).replaceChildren(...data.history[0].rows.map(r=>option(r.team,r.team+' · '+r.region)));$('#'+id).value=old|| (id==='teamA'?'GEN':'T1')}
 $('#error').hidden=true;render();renderDuel();renderHistory();
 }catch(e){$('#error').textContent='数据加载失败，请稍后刷新。通过本地预览服务打开此页面，直接双击 HTML 无法加载数据。';$('#error').hidden=false}
 finally{$('#refresh').disabled=false}
}
function render(){
 const snap=data.history[selected],rows=[...snap.rows].sort((a,b)=>b.champion-a.champion),leader=rows[0];$('#version').value=selected;
 $('#leaderName').textContent=leader.team;$('#leaderRegion').textContent=leader.region;$('#leaderProb').textContent=percent(leader.champion);$('#leaderBar').style.width=percent(leader.champion)+'%';
 const interval=data.intervals[leader.team];$('#leaderInterval').textContent=selected===0&&interval?`90% 模型重采样区间 ${percent(interval[0])}% — ${percent(interval[1])}%`:'赛果条件更新 · 本版本未重新估计模型区间';
 $('#cutoff').textContent=fmt(snap.cutoff);$('#accepted').textContent=(snap.state.swiss.length+snap.state.knockout.length)+' 场';$('#simulations').textContent=snap.n.toLocaleString();$('#checked').textContent=fmt(data.status.checked_at);
 $('#statusBadge').className='status-badge '+(data.status.state==='error'?'error':data.status.state==='waiting'?'waiting':'');$('#statusBadge').textContent=data.status.state==='error'?'更新暂停':data.status.state==='waiting'?'等待数据':'定时监测';
 $('#statusMessage').textContent=data.status.message;$('#sourceFreshness').textContent=`数据源最新比赛：${fmt(data.status.source_latest_at)} · 历史实力截止：${fmt(snap.model_cutoff)}${selected<data.history.length-1?' · 正在查看历史预测':''}`;
 renderRankings();renderMatches();
}
function renderRankings(){const snap=data.history[selected],query=$('#search').value.trim().toLowerCase(),region=$('#region').value,base=Object.fromEntries(data.history[0].rows.map(r=>[r.team,r]));
 const ranked=[...snap.rows].sort((a,b)=>b[stage]-a[stage]);$('#probHeading').textContent=labels[stage]+'概率';$('#rankings').replaceChildren();let count=0;
 ranked.forEach((r,index)=>{if(region&&region!==r.region||query&&!`${r.team} ${r.name}`.toLowerCase().includes(query))return;count++;
 const tr=el('tr');tr.append(el('td','',String(index+1).padStart(2,'0')));const cell=el('td'),team=el('div','team-cell'),name=el('div');name.append(el('div','team-name',r.team),el('div','team-sub',r.name+' · '+r.region));team.append(el('span','team-avatar',r.team),name);cell.append(team);tr.append(cell,el('td','',`${r.wins} – ${r.losses}`));
 const prob=el('td'),flex=el('div','prob-cell'),track=el('div','prob-track'),bar=el('i');bar.style.width=percent(r[stage])+'%';track.append(bar);flex.append(track,el('strong','',displayPercent(r[stage])+'%'));prob.append(flex);tr.append(prob);
 const diff=100*(r[stage]-base[r.team][stage]);tr.append(el('td','delta '+(Math.abs(diff)<.05?'':diff>0?'up':'down'),Math.abs(diff)<.05?'—':(diff>0?'+':'')+diff.toFixed(1)+' pp'));$('#rankings').append(tr)});$('#empty').hidden=count!==0;
}
function renderMatches(){const snap=data.history[selected],matches=[...snap.state.swiss.map(m=>({...m,label:'瑞士 R'+m.round})),...snap.state.knockout.map(m=>({...m,label:({quarterfinal:'八强赛',semifinal:'半决赛',final:'决赛'})[m.stage]}))];
 for(const match of data.first_round)if(!snap.state.swiss.some(m=>m.round===1&&[m.a,m.b].includes(match.a)))matches.push({...match,label:'首轮',pending:true});
 $('#matches').replaceChildren();for(const m of matches){const row=el('div','match-row');row.append(el('span','',m.label),el('b','',m.a+' / '+m.b));row.append(m.pending?el('small','',fmt(m.start_bjt).slice(5)+(m.start_time_is_approximate?' 约':'')):el('span','match-result',m.winner+' 胜 · '+m.score_a+':'+m.score_b));$('#matches').append(row)}
}
function renderDuel(){if(!data)return;const a=$('#teamA').value,b=$('#teamB').value,bo=Number($('#bestOf').value),target=$('#duelResult');target.replaceChildren();if(a===b){target.append(el('p','muted','请选择两支不同的队伍。'));return}let p=data.matrix[a][b];if(bo===3)p=3*p*p-2*p*p*p;else if(bo===5)p=10*p**3-15*p**4+6*p**5;
 const nums=el('div','duel-numbers');nums.append(el('span','',percent(p)+'%'),el('span','',percent(1-p)+'%'));const bar=el('div','duel-bar'),left=el('i'),right=el('i');left.style.width=percent(p)+'%';right.style.width=percent(1-p)+'%';bar.append(left,right);target.append(nums,bar);
}
function renderHistory(){const target=$('#historyList');target.replaceChildren();[...data.history].reverse().forEach(s=>{const row=el('div','history-item'),desc=el('div','',fmt(s.cutoff)+(s.kind==='frozen_baseline'?' · 赛前冻结':' · 赛果条件更新'));desc.append(el('small','',`采纳 ${s.state.swiss.length+s.state.knockout.length} 场赛果 · 版本 ${s.id.slice(0,8)}`));const btn=el('button','','查看此版本 →');btn.onclick=()=>{selected=data.history.indexOf(s);render();$('#overview').scrollIntoView({behavior:'smooth'})};row.append(desc,btn);target.append(row)});
 const trend=$('#trend');trend.replaceChildren();if(data.history.length===1){trend.append(el('p','','第一份预测已经保存。新赛果接入后，这里会显示各队夺冠概率的变化轨迹。'));return}
 const leaders=[...data.history.at(-1).rows].sort((a,b)=>b.champion-a.champion).slice(0,4),colors=['#9fc3ff','#eab58b','#86c9ae','#c2a3df'];const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 1000 230');svg.setAttribute('role','img');svg.setAttribute('aria-label','按预测版本顺序排列的夺冠概率趋势');
 const max=Math.max(.1,...data.history.flatMap(s=>s.rows.filter(r=>leaders.some(t=>t.team===r.team)).map(r=>r.champion)))*1.1;
 for(let i=0;i<4;i++){const line=document.createElementNS(svg.namespaceURI,'line');line.setAttribute('x1',45);line.setAttribute('x2',980);line.setAttribute('y1',15+i*60);line.setAttribute('y2',15+i*60);line.setAttribute('stroke','#293441');svg.append(line);const text=document.createElementNS(svg.namespaceURI,'text');text.textContent=(100*max*(1-i/3)).toFixed(0)+'%';text.setAttribute('x',0);text.setAttribute('y',20+i*60);text.setAttribute('fill','#9aaabb');text.setAttribute('font-size',12);svg.append(text)}
 leaders.forEach((t,i)=>{const points=data.history.map((s,j)=>`${45+j*935/(data.history.length-1)},${195-s.rows.find(r=>r.team===t.team).champion/max*180}`);const line=document.createElementNS(svg.namespaceURI,'polyline');line.setAttribute('points',points.join(' '));line.setAttribute('fill','none');line.setAttribute('stroke',colors[i]);line.setAttribute('stroke-width',2.5);svg.append(line)});trend.append(svg);const legend=el('div','legend');leaders.forEach((t,i)=>{const name=el('span','','● '+t.team);name.style.color=colors[i];legend.append(name)});legend.append(el('span','','横轴：预测版本顺序'));trend.append(legend);
}
$('#refresh').onclick=fetchData;$('#version').onchange=e=>{selected=Number(e.target.value);render()};$('#search').oninput=renderRankings;$('#region').onchange=renderRankings;
for(const btn of document.querySelectorAll('[data-stage]'))btn.onclick=()=>{stage=btn.dataset.stage;for(const other of document.querySelectorAll('[data-stage]')){other.classList.toggle('active',other===btn);other.setAttribute('aria-pressed',String(other===btn))}renderRankings()};
for(const id of ['teamA','teamB','bestOf'])$('#'+id).onchange=renderDuel;
$('#download').onclick=()=>{if(!data)return;const snap=data.history[selected],url=URL.createObjectURL(new Blob([JSON.stringify(snap,null,2)],{type:'application/json'}));const a=el('a');a.href=url;a.download='forecast-'+snap.id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)};
fetchData();setInterval(fetchData,60000);
