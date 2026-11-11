from __future__ import annotations
import base64, hashlib, json, os, secrets, shutil, sqlite3, threading, time, subprocess, sys
import re, unicodedata
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE = Path(__file__).resolve().parent.parent
LIB = BASE / '本地视频库'
DB = LIB / '索引' / 'library.sqlite3'
CONFIG = LIB / '索引' / 'web_config.json'
HOST = os.environ.get('DOUYIN_HOST', '0.0.0.0')
PORT = int(os.environ.get('DOUYIN_PORT', '8765'))

def load_config():
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    if CONFIG.exists():
        return json.loads(CONFIG.read_text(encoding='utf-8'))
    cfg = {'management_password': secrets.token_urlsafe(12), 'created_at': time.strftime('%Y-%m-%dT%H:%M:%S%z')}
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
    return cfg

CFG = load_config()
SESSIONS = {}
JOBS = {}
LOCK = threading.Lock()

def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute('''create table if not exists subscriptions(
        creator_id text primary key references creators(id), name text, homepage_url text,
        platform_id text, start_date text, mode text default 'since_date',
        status text default 'active', last_checked_at text, last_success_at text,
        created_at text, note text)''')
    c.commit()
    return c

def rows(c, sql, args=()): return [dict(x) for x in c.execute(sql, args)]
def normalize_creator(name):
    s=unicodedata.normalize('NFKC',str(name or '')).strip().lower()
    s=re.sub(r'[\s\u3000]+','',s)
    s=re.sub(r'[·•●★☆_\-—~～]+','',s)
    return s

def safe_path(p):
    p = Path(p).resolve()
    return p if p.is_relative_to(LIB.resolve()) else None

INDEX = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>本地抖音工作台</title>
<style>body{margin:0;font:15px/1.5 system-ui,"Microsoft YaHei",sans-serif;background:#f5f6fa;color:#202535}header{background:#181b27;color:white;padding:14px 22px;display:flex;justify-content:space-between;align-items:center}main{max-width:1400px;margin:auto;padding:18px}.tabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}.tabs button,button{border:0;border-radius:8px;padding:9px 13px;cursor:pointer;background:#e6eaf3}.tabs button.active,button.primary{background:#315bdc;color:#fff}.panel{background:#fff;border:1px solid #e0e4ed;border-radius:12px;padding:16px;margin-bottom:16px}.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}.stat{background:#f7f8fc;padding:12px;border-radius:9px}.stat b{display:block;font-size:24px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}.card{border:1px solid #e0e4ed;border-radius:10px;padding:12px;background:#fff}.card h3{margin:4px 0;font-size:16px}.muted{color:#6d7484}.danger{color:#b3261e}.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}input,select{padding:9px;border:1px solid #bcc5d6;border-radius:7px;font:inherit}.hidden{display:none}video{width:100%;max-height:260px;background:#111;border-radius:8px}.creatorbar{height:5px;background:#3c65d9;border-radius:4px;margin:-12px -12px 9px}.row{display:flex;justify-content:space-between;gap:8px;align-items:center}small{overflow-wrap:anywhere;color:#687187}.badge{font-size:12px;padding:3px 7px;border-radius:999px;background:#eef1f8}.dangerbtn{background:#b3261e;color:#fff}.playerShell{max-width:560px;margin:auto;background:#111;color:#fff;border-radius:14px;overflow:hidden;position:relative}.playerShell.immersive{position:fixed;inset:0;z-index:9999;max-width:none;width:100vw;height:100vh;border-radius:0}.playerShell.immersive .feedViewport{height:100vh;min-height:0}.playerShell:fullscreen{max-width:none;width:100vw;height:100vh;border-radius:0}.playerShell:fullscreen .feedViewport{height:100vh;min-height:0}.playerShell.immersive .playerTop{position:absolute;inset:0 0 auto;z-index:5;background:linear-gradient(rgba(0,0,0,.38),transparent);pointer-events:none}.playerShell.immersive .playerTop select,.playerShell.immersive #modeButton,.playerShell.immersive #reason,.playerShell.immersive #modeNotice{display:none}.playerShell.immersive #fullscreenButton{display:block;margin-left:auto;pointer-events:auto;background:rgba(0,0,0,.45);color:#fff;font-size:22px;border-radius:50%;width:42px;height:42px;padding:0}.playerShell.immersive .videoOverlay h2,.playerShell.immersive .videoOverlay p,.playerShell.immersive .videoOverlay .why{display:none}.playerShell.immersive .videoOverlay{padding:18px;background:none}.playerShell.immersive .videoOverlay .actions{justify-content:flex-end}.playerShell:fullscreen .playerTop{position:absolute;inset:0 0 auto;z-index:5;background:linear-gradient(rgba(0,0,0,.38),transparent);pointer-events:none}.playerShell:fullscreen .playerTop select,.playerShell:fullscreen #modeButton,.playerShell:fullscreen #reason,.playerShell:fullscreen #modeNotice{display:none}.playerShell:fullscreen #fullscreenButton{display:block;margin-left:auto;pointer-events:auto;background:rgba(0,0,0,.45);color:#fff;font-size:22px;border-radius:50%;width:42px;height:42px;padding:0}.playerShell:fullscreen .videoOverlay h2,.playerShell:fullscreen .videoOverlay p,.playerShell:fullscreen .videoOverlay .why{display:none}.playerShell:fullscreen .videoOverlay{padding:18px;background:none}.playerShell:fullscreen .videoOverlay .actions{justify-content:flex-end}.playerTop{padding:12px;background:#181b27;display:flex;justify-content:space-between;align-items:center;gap:8px}.playerTop #reason{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.feedViewport{height:min(78vh,780px);min-height:520px;position:relative;background:#000;overflow:hidden;outline:none;touch-action:pan-y;user-select:none}.feedViewport>video{width:100%;height:100%;max-height:none;object-fit:contain;transition:opacity .16s ease}.videoOverlay{position:absolute;inset:auto 0 0;padding:70px 18px 18px;background:linear-gradient(transparent,rgba(0,0,0,.86));pointer-events:none}.videoOverlay h2,.videoOverlay p{margin:4px 0;text-shadow:0 1px 3px #000}.videoOverlay .actions{display:flex;gap:7px;flex-wrap:wrap;margin-top:12px;pointer-events:auto}.videoOverlay button{background:rgba(255,255,255,.9);color:#202535}.playerHelp{padding:10px;text-align:center;background:#181b27;color:#b9c1d4;font-size:12px}@keyframes heart{0%{opacity:0;transform:translate(-50%,-50%) scale(.5)}30%{opacity:1}100%{opacity:0;transform:translate(-50%,-50%) scale(1.25)}}@media(max-width:600px){body:has(.playerShell) header,body:has(.playerShell) main{padding:0}body:has(.playerShell) main{max-width:none}.playerShell{max-width:none;border-radius:0;height:calc(100vh - 52px)}.feedViewport{height:calc(100vh - 145px);min-height:420px}.playerShell .playerHelp{display:none}.playerTop{padding:9px 10px}}
</style>
<header><strong>本地抖音工作台</strong><span id="mode">浏览模式</span></header><main><div id="login" class="panel"><h2>进入工作台</h2><p>浏览可直接进入；管理操作需要固定管理密码。</p><input id="pw" type="password" placeholder="管理密码"><button class="primary" onclick="login()">进入管理模式</button><button onclick="browse()">浏览模式</button><p id="loginmsg" class="danger"></p></div><div id="app" class="hidden"><nav class="tabs"><button data-tab="home" class="active">总览</button><button data-tab="creators">博主整理</button><button data-tab="subscriptions">自动更新</button><button data-tab="review">待确认</button><button data-tab="player" onclick="location.href='/player/'">播放器</button></nav><section id="home" class="tab"></section><section id="creators" class="tab hidden"></section><section id="subscriptions" class="tab hidden"></section><section id="review" class="tab hidden"></section><section id="player" class="tab hidden"></section></div></main>
<script>
let token='',admin=false,cache={};
const $=x=>document.querySelector(x), esc=x=>String(x??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
async function api(path,opt={}){opt.headers={...(opt.headers||{}),'Content-Type':'application/json',...(token?{'X-Admin-Token':token}:{})};let r=await fetch(path,opt);if(!r.ok)throw Error(await r.text());return r.json()}
function browse(){admin=false;$('#login').classList.add('hidden');$('#app').classList.remove('hidden');load()}
async function login(){try{let x=await api('/api/login',{method:'POST',body:JSON.stringify({password:$('#pw').value})});token=x.token;admin=true;$('#login').classList.add('hidden');$('#app').classList.remove('hidden');$('#mode').textContent='管理模式';load()}catch(e){$('#loginmsg').textContent='密码不正确'}}
document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{document.querySelectorAll('[data-tab]').forEach(x=>x.classList.remove('active'));b.classList.add('active');document.querySelectorAll('.tab').forEach(x=>x.classList.add('hidden'));$('#'+b.dataset.tab).classList.remove('hidden');render(b.dataset.tab)});
async function load(){cache.summary=await api('/api/summary');cache.creators=await api('/api/creators');cache.files=await api('/api/files?limit=100');cache.similar=await api('/api/similar');render('home')}
function render(tab){if(tab==='home')renderHome();if(tab==='creators')renderCreators();if(tab==='subscriptions')renderSubscriptions();if(tab==='review')renderReview();if(tab==='player')renderPlayer()}
function renderHome(){let s=cache.summary;$('#home').innerHTML='<div class="panel"><h2>工作台总览</h2><div class="stats">'+[['可用文件',s.available],['博主身份',s.creators],['作品',s.works],['重复副本',s.duplicates],['相似候选',s.similar],['待确认文件',s.unknown]].map(x=>'<div class="stat">'+x[0]+'<b>'+x[1]+'</b></div>').join('')+'</div></div><div class="panel"><h3>建议顺序</h3><p>先处理博主身份和相似候选，再进入播放器。所有管理动作都要在管理模式确认。</p><button onclick="go(&quot;creators&quot;)">开始博主整理</button> <button onclick="go(&quot;review&quot;)">查看待确认</button></div>'}
function go(t){document.querySelector('[data-tab="'+t+'"]').click()}
function renderCreators(){let q=cache.creatorQ||'';let list=cache.creators.filter(x=>!q||(x.name||'').toLowerCase().includes(q.toLowerCase())||x.id.toLowerCase().includes(q.toLowerCase()));$('#creators').innerHTML='<div class="panel"><h2>博主整理</h2><div class="toolbar"><input id="cq" value="'+esc(q)+'" placeholder="搜索博主名或编号"><button id="creatorSearchButton">搜索</button><button id="candidateButton">名称合并候选</button><span class="muted">同名身份默认分开</span></div><div id="candidatePanel"></div><div class="grid">'+list.map(x=>'<article class="card"><div class="creatorbar"></div><h3>'+esc(x.name||'博主待确认')+'</h3><p>文件 '+x.file_count+' · 视频 '+x.video_count+' · 图片 '+x.image_count+'</p><small>'+esc(x.id)+'<br>'+esc(x.identity_status||'')+'</small>'+(admin?'<p><button class="editCreatorButton" data-id="'+esc(x.id)+'">修改归类</button></p>':'')+'</article>').join('')+'</div></div>';$('#creatorSearchButton').onclick=creatorSearch;$('#candidateButton').onclick=showCandidates;document.querySelectorAll('.editCreatorButton').forEach(b=>b.onclick=()=>editCreator(b.dataset.id))}
async function showCandidates(){let x=await api('/api/creator-candidates');let cards=x.map(g=>{let people=g.items.map(i=>esc(i.name)+' · '+i.file_count+' 个文件 · '+esc(i.id)).join('<br>');let action=admin?'<button class="mergeButton" data-a="'+g.items[0].id+'" data-b="'+g.items[1].id+'">合并到第一个身份</button>':'';return '<div class="card"><b>'+esc(g.normalized_name)+'</b><p>'+people+'</p>'+action+' <button class="keepButton">保持分开</button></div>'}).join('');$('#candidatePanel').innerHTML='<div class="panel"><h3>名称合并候选</h3>'+(cards||'<p>暂无规范化名称相同的候选。</p>')+'</div>';document.querySelectorAll('.mergeButton').forEach(b=>b.onclick=()=>mergeCreators(b.dataset.a,b.dataset.b));document.querySelectorAll('.keepButton').forEach(b=>b.onclick=e=>e.target.closest('.card').remove())}
async function mergeCreators(a,b){if(!confirm('确认合并这两个博主身份？原始记录会保留为别名。'))return;await api('/api/creator-merge',{method:'POST',body:JSON.stringify({primary:a,secondary:b})});cache.creators=await api('/api/creators');renderCreators();alert('已合并，原身份已保留为别名记录')}
function creatorSearch(){cache.creatorQ=$('#cq').value;renderCreators()}
async function renderSubscriptions(){let x=await api('/api/subscriptions');let options=cache.creators.map(c=>'<option value="'+esc(c.id)+'">'+esc(c.name||'博主待确认')+' · '+esc(c.id)+'</option>').join('');$('#subscriptions').innerHTML='<div class="panel"><h2>博主自动更新</h2><p>选择博主和起始日期，生成给下载工具使用的任务文件。下载完成后请放入待导入文件夹。</p><div class="toolbar"><select id="subCreator">'+options+'</select><input id="subUrl" placeholder="博主主页链接（可选）"><input id="subDate" type="date"><button id="addSub">加入订阅</button></div><div id="subList" class="grid">'+x.map(s=>'<article class="card"><h3>'+esc(s.name)+'</h3><p>起始日期：'+esc(s.start_date||'未设置')+' · 状态：'+esc(s.status)+'</p><small>'+esc(s.homepage_url||'未填写主页链接')+'<br>上次检查：'+esc(s.last_checked_at||'尚未检查')+'</small><p><button class="taskButton" data-id="'+esc(s.creator_id)+'">生成下载任务</button></p></article>').join('')+'</div></div><div id="taskResult"></div>';$('#addSub').onclick=addSubscription;document.querySelectorAll('.taskButton').forEach(b=>b.onclick=()=>exportTask(b.dataset.id))}
async function addSubscription(){if(!admin){alert('请先进入管理模式');return}let r=await api('/api/subscriptions',{method:'POST',body:JSON.stringify({creator_id:$('#subCreator').value,homepage_url:$('#subUrl').value,start_date:$('#subDate').value})});renderSubscriptions();alert(r.message||'已加入订阅')}
async function exportTask(id){if(!admin){alert('请先进入管理模式');return}let r=await api('/api/export-task',{method:'POST',body:JSON.stringify({creator_id:id})});$('#taskResult').innerHTML='<div class="panel"><h3>任务已生成</h3><p>任务编号：'+esc(r.task_id)+'</p><p>CSV：'+esc(r.csv)+'<br>JSON：'+esc(r.json)+'<br>下载目录：'+esc(r.download_dir)+'</p><button class="primary" id="runTask">立即开始下载</button><span id="taskStatus" class="muted"></span></div>';$('#runTask').onclick=()=>runTask(r.task_id)}
async function runTask(id){if(!admin)return;let r=await api('/api/run-task',{method:'POST',body:JSON.stringify({task_id:id})});$('#taskStatus').textContent=r.message||'已启动';let timer=setInterval(async()=>{let s=await api('/api/task-status?task_id='+encodeURIComponent(id));$('#taskStatus').textContent=' · '+(s.status||'运行中');if(['完成','失败'].includes(s.status))clearInterval(timer)},1500)}
async function editCreator(id){let name=prompt('输入新的博主名；留空取消');if(!name)return;await api('/api/creator/'+id,{method:'POST',body:JSON.stringify({name})});cache.creators=await api('/api/creators');renderCreators()}
function renderReview(){let files=cache.files.filter(x=>x.status!=='已永久删除');$('#review').innerHTML='<div class="panel"><h2>待确认与异常</h2><p>当前只显示未删除记录。</p><div class="grid">'+files.slice(0,60).map(x=>'<article class="card"><h3>'+esc(x.title||x.name||'未命名')+'</h3><p>'+esc(x.creator||'博主待确认')+' · '+esc(x.media_status||x.status||'')+'</p><small>'+esc(x.current_path)+'</small></article>').join('')+'</div></div><div class="panel"><h2>相似候选</h2>'+cache.similar.map(x=>'<div class="card"><p>'+esc(x.reason)+' · 参考值 '+x.score+'</p><button class="similarReview" data-a="'+esc(x.a)+'" data-b="'+esc(x.b)+'" data-decision="同一作品的不同版本">同一作品版本</button> <button class="similarReview" data-a="'+esc(x.a)+'" data-b="'+esc(x.b)+'" data-decision="不同作品">不同作品</button> <button class="similarReview" data-a="'+esc(x.a)+'" data-b="'+esc(x.b)+'" data-decision="暂不确定">暂不确定</button></div>').join('')+'</div>';document.querySelectorAll('.similarReview').forEach(b=>b.onclick=()=>review(b.dataset.a,b.dataset.b,b.dataset.decision))}
async function review(a,b,decision){if(!admin){alert('请先进入管理模式');return}await api('/api/review',{method:'POST',body:JSON.stringify({a,b,decision})});cache.similar=await api('/api/similar');renderReview()}
let feed=[],feedIndex=0,currentVideo=null,mode='recommend',sessionMode='test',tempBlockedVideos=new Set(),tempBlockedCreators=new Set(),preloadedVideos=new Map(),clickTimer=null;
function renderPlayer(){
 $('#player').innerHTML='<div class="playerShell"><div class="playerTop"><select id="feedMode"><option value="recommend">推荐</option><option value="creator">博主轮播</option><option value="random">随机探索</option></select><button id="fullscreenButton" title="全屏">⛶</button><button id="modeButton">管理/测试模式</button><span id="reason"></span></div><div id="modeNotice" class="modeNotice">管理/测试模式：不会影响真实推荐</div><div id="feedViewport" class="feedViewport" tabindex="0"></div><div class="playerHelp">↑↓ / 滚轮切换　双击喜欢　←返回　空格暂停</div></div>';
 $('#feedMode').value=mode;$('#feedMode').onchange=()=>{mode=$('#feedMode').value;startFeed()};$('#modeButton').onclick=toggleRealMode;$('#fullscreenButton').onclick=toggleFullscreen;
 let vp=$('#feedViewport');vp.onwheel=e=>{e.preventDefault();step(e.deltaY>0?1:-1)};vp.onkeydown=e=>{if(e.key==='ArrowDown')step(1);if(e.key==='ArrowUp')step(-1);if(e.key===' '){e.preventDefault();let v=vp.querySelector('video');if(v)v.paused?v.play():v.pause()}};
 let startY=0,startT=0,swiping=false;vp.ontouchstart=e=>{startY=e.touches[0].clientY;startT=Date.now();swiping=false};vp.ontouchmove=e=>{if(Math.abs(e.touches[0].clientY-startY)>12)swiping=true};vp.ontouchend=e=>{let d=startY-e.changedTouches[0].clientY,dt=Date.now()-startT;if(swiping&&Math.abs(d)>Math.max(48,window.innerHeight*.12)&&dt<700)step(d>0?1:-1)};
 startFeed();
}
async function startFeed(){try{for(let v of preloadedVideos.values())v.src='';preloadedVideos.clear();feed=await api('/api/feed?mode='+mode);feed=filterTemp(feed);feedIndex=0;showFeed()}catch(e){$('#feedViewport').innerHTML='<p class="danger">推荐流加载失败：'+esc(e.message)+'</p>'}}
function filterTemp(list){return list.filter(x=>!tempBlockedVideos.has(x.file_id)&&!tempBlockedCreators.has(x.creator_id))}
function syncFullscreen(){let shell=document.querySelector('.playerShell');if(!shell)return;let active=document.fullscreenElement===shell||shell.classList.contains('immersive');shell.classList.toggle('immersive',active&&!document.fullscreenElement);$('#fullscreenButton').textContent=active?'×':'⛶';$('#fullscreenButton').title=active?'退出全屏':'全屏'}
document.addEventListener('fullscreenchange',syncFullscreen);
function toggleFullscreen(){let shell=document.querySelector('.playerShell');if(!shell)return;if(document.fullscreenElement||shell.classList.contains('immersive')){if(document.fullscreenElement&&document.exitFullscreen)document.exitFullscreen().catch(()=>{});shell.classList.remove('immersive');syncFullscreen();document.body.style.overflow='';return}document.body.style.overflow='hidden';if(shell.requestFullscreen){shell.requestFullscreen().then(syncFullscreen).catch(()=>{shell.classList.add('immersive');syncFullscreen()})}else{shell.classList.add('immersive');syncFullscreen()}}
function toggleRealMode(){if(sessionMode==='real'){sessionMode='test';$('#modeButton').textContent='管理/测试模式';$('#modeNotice').textContent='管理/测试模式：不会影响真实推荐';$('#modeNotice').className='modeNotice';return}if(!confirm('进入真实推荐模式后，观看、喜欢、跳过和屏蔽会影响推荐结果。确定进入？'))return;sessionMode='real';$('#modeButton').textContent='真实推荐模式';$('#modeNotice').textContent='真实推荐模式：行为会影响推荐';$('#modeNotice').className='modeNotice real'}
function showFeed(){let x=feed[feedIndex];if(!x){$('#feedViewport').innerHTML='<p>没有符合条件的视频。</p>';return}currentVideo=x;let vp=$('#feedViewport');vp.innerHTML='<video id="activeVideo" playsinline muted preload="auto"></video><div class="videoOverlay"><h2>'+esc(x.title||'未命名')+'</h2><p>@'+esc(x.creator||'博主待确认')+' · '+(x.duration||'')+' 秒</p><p class="why">'+esc(x.reason||'来自本地视频库')+'</p><div class="actions"><button id="likeBtn">♡ 喜欢</button><button id="blockVideo">不喜欢此视频</button><button id="blockCreator">不喜欢此博主</button><button id="nextBtn">下一个</button></div></div>';let v=$('#activeVideo'),warm=preloadedVideos.get(feedIndex);if(warm){preloadedVideos.delete(feedIndex);warm.id='activeVideo';warm.playsInline=true;v.replaceWith(warm);v=warm}else{v.src='/media?path='+encodeURIComponent(x.current_path)}v.muted=localStorage.getItem('muted')==='1';v.onplay=()=>sendEvent('play_start');v.ontimeupdate=()=>{if(v.currentTime>3&&!x.counted){x.counted=true;sendEvent('watch_start',{watch_ms:Math.round(v.currentTime*1000)})}};v.onended=()=>{sendEvent('complete',{watch_ms:Math.round(v.duration*1000)});step(1)};v.play().catch(()=>{});vp.onclick=e=>{if(e.target.closest('button'))return;clearTimeout(clickTimer);clickTimer=setTimeout(()=>{let active=$('#activeVideo');if(active)active.paused?active.play().catch(()=>{}):active.pause()},220)};vp.ondblclick=e=>{if(e.target.closest('button'))return;clearTimeout(clickTimer);sendEvent('like');$('#likeBtn').textContent='♥ 已喜欢';showHeart()};$('#likeBtn').onclick=()=>sendEvent('like');$('#blockVideo').onclick=()=>block('video');$('#blockCreator').onclick=()=>block('creator');$('#nextBtn').onclick=()=>step(1);$('#reason').textContent=x.reason||'';preload(1);preload(-1)}
function showHeart(){let h=document.createElement('div');h.textContent='♥';h.style='position:absolute;left:50%;top:42%;font-size:90px;color:#ff3b6b;transform:translate(-50%,-50%);animation:heart .55s ease-out forwards;pointer-events:none';$('#feedViewport').appendChild(h);setTimeout(()=>h.remove(),650)}
let lastStepAt=0;function step(delta){if(!feed.length||Date.now()-lastStepAt<260)return;lastStepAt=Date.now();let v=$('#activeVideo');if(v)sendEvent(delta>0?'skip':'rewind',{watch_ms:Math.round((v.currentTime||0)*1000)});feedIndex=Math.max(0,Math.min(feed.length-1,feedIndex+delta));showFeed()}
function preload(d){let i=feedIndex+d,x=feed[i];if(!x||preloadedVideos.has(i))return;let v=document.createElement('video');v.preload=d>0?'auto':'metadata';v.muted=true;v.src='/media?path='+encodeURIComponent(x.current_path);v.load();preloadedVideos.set(i,v);if(preloadedVideos.size>4){let old=[...preloadedVideos.keys()].sort((a,b)=>Math.abs(a-feedIndex)-Math.abs(b-feedIndex)).pop();let stale=preloadedVideos.get(old);stale.src='';preloadedVideos.delete(old)}}
async function sendEvent(event_type,extra={}){if(!currentVideo||sessionMode!=='real')return;try{await api('/api/event',{method:'POST',body:JSON.stringify({file_id:currentVideo.file_id,work_id:currentVideo.work_id,event_type,watch_ms:extra.watch_ms||0,mode:sessionMode,extra})})}catch(e){}}
async function block(kind){if(!currentVideo)return;let label=kind==='creator'?'这个博主的全部视频':'这个视频';if(!confirm('以后不再推荐'+label+'？'))return;if(kind==='creator')tempBlockedCreators.add(currentVideo.creator_id);else tempBlockedVideos.add(currentVideo.file_id);await sendEvent(kind==='creator'?'block_creator':'block_video');feed=filterTemp(await api('/api/feed?mode='+mode));feedIndex=Math.min(feedIndex,Math.max(0,feed.length-1));showFeed()}
load().catch(()=>{});
</script></html>'''

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass
    def send_json(self, obj, status=200):
        data=json.dumps(obj,ensure_ascii=False).encode(); self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
    def body(self):
        n=int(self.headers.get('Content-Length','0')); return json.loads(self.rfile.read(n) or b'{}')
    def admin(self): return self.headers.get('X-Admin-Token') in SESSIONS
    def do_GET(self):
        u=urlparse(self.path)
        if u.path.startswith('/player'):
            rel=u.path.removeprefix('/player').lstrip('/') or 'index.html'
            player_root=(BASE/'整理工具'/'player-web'/'dist').resolve()
            asset=(player_root/rel).resolve()
            if not asset.is_relative_to(player_root) or not asset.is_file():
                self.send_error(404); return
            content=asset.read_bytes()
            types={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.svg':'image/svg+xml'}
            self.send_response(200); self.send_header('Content-Type',types.get(asset.suffix,'application/octet-stream')); self.send_header('Content-Length',str(len(content))); self.end_headers(); self.wfile.write(content); return
        if u.path in ('/','/index.html'):
            b=INDEX.encode(); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b); return
        if u.path=='/api/task-status':
            task_id=parse_qs(u.query).get('task_id',[''])[0]
            self.send_json(JOBS.get(task_id, {'task_id':task_id,'status':'未启动'})); return
        c=db()
        try:
            if u.path=='/api/summary':
                x=c.execute("select count(*) n from files where status!='已永久删除'").fetchone()['n']; self.send_json({'available':x,'creators':c.execute('select count(*) n from creators').fetchone()['n'],'works':c.execute('select count(*) n from works').fetchone()['n'],'duplicates':c.execute("select count(*) n from files where duplicate_of is not null and status!='已永久删除'").fetchone()['n'],'similar':c.execute('select count(*) n from similar').fetchone()['n'],'unknown':c.execute("select count(*) n from files where creator_id is null and status!='已永久删除'").fetchone()['n']}); return
            if u.path=='/api/creators': self.send_json(rows(c,"select c.id,c.name,c.platform_id,c.identity_status,count(f.id) file_count,sum(case when f.kind='video' then 1 else 0 end) video_count,sum(case when f.kind='image' then 1 else 0 end) image_count from creators c left join files f on f.creator_id=c.id and f.status!='已永久删除' group by c.id order by file_count desc,c.name")); return
            if u.path=='/api/creator-candidates':
                items=rows(c,"select c.id,c.name,count(f.id) file_count from creators c left join files f on f.creator_id=c.id and f.status!='已永久删除' group by c.id")
                groups={}
                for x in items: groups.setdefault(normalize_creator(x['name']),[]).append(x)
                self.send_json([{'normalized_name':k,'items':v} for k,v in groups.items() if k and len(v)>1]); return
            if u.path=='/api/subscriptions':
                self.send_json(rows(c,"select s.*,coalesce(s.name,cr.name,'博主待确认') name from subscriptions s left join creators cr on cr.id=s.creator_id order by s.status desc,s.name")); return
            if u.path=='/api/files': self.send_json(rows(c,"select f.id,f.current_path,f.status,f.kind,f.duplicate_of,w.title,coalesce(cr.name,'博主待确认') creator,m.status media_status,m.duration from files f left join works w on w.id=f.work_id left join creators cr on cr.id=f.creator_id left join media m on m.file_id=f.id where f.status!='已永久删除' order by f.id limit 100")); return
            if u.path=='/api/feed':
                mode=parse_qs(u.query).get('mode',['recommend'])[0]
                page=max(0,int(parse_qs(u.query).get('page',['0'])[0] or 0))
                limit=min(30,max(1,int(parse_qs(u.query).get('limit',['12'])[0] or 12)))
                order={'random':'random()','creator':'f.creator_id,random()','recommend':'random()'}.get(mode,'random()')
                sql=f'''select f.id file_id,f.work_id,f.creator_id,f.current_path,f.status,w.title,coalesce(cr.name,'博主待确认') creator,m.duration,
                    case when ?='creator' then '博主轮播' when ?='random' then '随机探索' else '本地推荐' end reason
                    from files f left join works w on w.id=f.work_id left join creators cr on cr.id=f.creator_id left join media m on m.file_id=f.id
                    where f.kind='video' and f.status!='已永久删除' and f.duplicate_of is null
                    and coalesce(m.status,'') not in ('仅音频，扩展名为MP4','下载未完成','无法抽帧','检查失败')
                    and not exists(select 1 from feedback b where b.event_type='block_video' and b.extra_json like '%"file_id":"'||f.id||'"%')
                    and not exists(select 1 from feedback b where b.event_type='block_creator' and b.extra_json like '%"creator_id":"'||coalesce(f.creator_id,'')||'"%')
                    order by {order} limit ? offset ?'''
                self.send_json(rows(c,sql,(mode,mode,limit,page*limit))); return
            if u.path=='/api/similar': self.send_json(rows(c,'select * from similar order by score desc')); return
            if u.path=='/media':
                p=safe_path(parse_qs(u.query).get('path',[''])[0]);
                if not p or not p.is_file(): self.send_error(404); return
                size=p.stat().st_size; start=0; end=size-1; range_header=self.headers.get('Range','')
                if range_header.startswith('bytes='):
                    spec=range_header[6:].split(',',1)[0].strip(); a,b=(spec.split('-',1)+[''])[:2]
                    if a: start=int(a); end=int(b) if b else size-1
                    else: start=max(0,size-int(b)); end=size-1
                    if start>=size or start>end: self.send_response(416); self.send_header('Content-Range',f'bytes */{size}'); self.end_headers(); return
                    end=min(end,size-1); self.send_response(206); self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
                else: self.send_response(200)
                length=end-start+1; self.send_header('Accept-Ranges','bytes'); self.send_header('Content-Type','video/mp4'); self.send_header('Content-Length',str(length)); self.end_headers()
                with p.open('rb') as f:
                    f.seek(start); remaining=length
                    while remaining:
                        chunk=f.read(min(1024*1024,remaining))
                        if not chunk: break
                        self.wfile.write(chunk); remaining-=len(chunk)
                return
            self.send_error(404)
        finally: c.close()
    def do_POST(self):
        u=urlparse(self.path); data=self.body()
        if u.path=='/api/login':
            if secrets.compare_digest(str(data.get('password','')),str(CFG['management_password'])):
                t=secrets.token_urlsafe(24); SESSIONS[t]=time.time(); self.send_json({'token':t}); return
            self.send_json({'error':'密码错误'},401); return
        if u.path=='/api/event':
            if data.get('mode')!='real':
                self.send_json({'ok':True,'recorded':False,'mode':'test'}); return
            c=db()
            try:
                import uuid,datetime
                file_id=data.get('file_id'); fr=c.execute('select work_id,creator_id from files where id=?',(file_id,)).fetchone()
                if not fr: self.send_json({'error':'文件不存在'},404); return
                extra=dict(data.get('extra') or {}); extra.update(file_id=file_id,creator_id=fr['creator_id'])
                c.execute('insert into feedback(id,work_id,event_at,event_type,watch_ms,session_id,recommender_version,extra_json) values(?,?,?,?,?,?,?,?)',(uuid.uuid4().hex,fr['work_id'],datetime.datetime.now().astimezone().isoformat(timespec='seconds'),data.get('event_type','play_start'),int(data.get('watch_ms') or 0),self.headers.get('X-Session','local'), 'feed-v1',json.dumps(extra,ensure_ascii=False)))
                c.commit(); self.send_json({'ok':True}); return
            finally: c.close()
        if not self.admin(): self.send_json({'error':'需要管理权限'},403); return
        c=db()
        try:
            if u.path.startswith('/api/creator/'):
                cid=u.path.rsplit('/',1)[-1]; old=c.execute('select * from creators where id=?',(cid,)).fetchone();
                if not old: self.send_json({'error':'找不到博主'},404); return
                c.execute('update creators set name=?,identity_status=? where id=?',(str(data.get('name') or old['name']),'人工确认',cid)); c.commit(); self.send_json({'ok':True}); return
            if u.path=='/api/creator-merge':
                primary,secondary=data.get('primary'),data.get('secondary')
                a=c.execute('select * from creators where id=?',(primary,)).fetchone(); b=c.execute('select * from creators where id=?',(secondary,)).fetchone()
                if not a or not b or primary==secondary: self.send_json({'error':'博主身份不存在'},400); return
                aliases=[]
                for raw in (a['aliases_json'],b['aliases_json']):
                    try: aliases.extend(json.loads(raw or '[]'))
                    except Exception: pass
                aliases.extend([{'id':b['id'],'name':b['name']}])
                c.execute('update creators set aliases_json=?,identity_status=? where id=?',(json.dumps(aliases,ensure_ascii=False),'人工确认',primary))
                c.execute('update files set creator_id=? where creator_id=?',(primary,secondary)); c.execute('update works set creator_id=? where creator_id=?',(primary,secondary)); c.execute('insert into review_log(created_at,object_type,object_id,old_json,new_json) values(?,?,?,?,?)',(time.strftime('%Y-%m-%dT%H:%M:%S%z'),'creator_merge',primary,json.dumps({'secondary':secondary,'name':b['name']},ensure_ascii=False),json.dumps({'primary':primary},ensure_ascii=False))); c.commit(); self.send_json({'ok':True}); return
            if u.path=='/api/subscriptions':
                import datetime
                cr=c.execute('select id,name,platform_id from creators where id=?',(data.get('creator_id'),)).fetchone()
                if not cr: self.send_json({'error':'博主不存在'},404); return
                c.execute('insert into subscriptions(creator_id,name,homepage_url,platform_id,start_date,mode,status,created_at) values(?,?,?,?,?,?,?,?) on conflict(creator_id) do update set name=excluded.name,homepage_url=excluded.homepage_url,platform_id=excluded.platform_id,start_date=excluded.start_date,status=\'active\'',(cr['id'],cr['name'],data.get('homepage_url',''),cr['platform_id'],data.get('start_date',''),'since_date','active',datetime.datetime.now().astimezone().isoformat(timespec='seconds'))); c.commit(); self.send_json({'ok':True,'message':'订阅已保存'}); return
            if u.path=='/api/export-task':
                import datetime,uuid,csv
                s=c.execute('select * from subscriptions where creator_id=?',(data.get('creator_id'),)).fetchone()
                if not s: self.send_json({'error':'请先加入订阅'},400); return
                task_id='task_'+datetime.datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]; root=LIB/'更新任务'; root.mkdir(parents=True,exist_ok=True); csv_path=root/(task_id+'.csv'); json_path=root/(task_id+'.json')
                task={'task_id':task_id,'creator_id':s['creator_id'],'creator_name':s['name'],'homepage_url':s['homepage_url'],'platform_id':s['platform_id'],'start_date':s['start_date'],'end_date':'','download_dir':str(LIB/'待导入'/str(s['name'] or s['creator_id'])),'created_at':datetime.datetime.now().astimezone().isoformat(timespec='seconds'),'status':'待下载','runner':'整理工具/运行下载任务.ps1'}
                json_path.write_text(json.dumps(task,ensure_ascii=False,indent=2),encoding='utf-8');
                with csv_path.open('w',encoding='utf-8-sig',newline='') as f: w=csv.DictWriter(f,fieldnames=list(task)); w.writeheader(); w.writerow(task)
                self.send_json({'ok':True,'task_id':task_id,'csv':str(csv_path),'json':str(json_path),'download_dir':task['download_dir']}); return
            if u.path=='/api/run-task':
                task_id=str(data.get('task_id') or '')
                task_path=(LIB/'更新任务'/f'{task_id}.json').resolve()
                if not re.fullmatch(r'task_[A-Za-z0-9_]+',task_id) or not task_path.is_relative_to((LIB/'更新任务').resolve()) or not task_path.exists():
                    self.send_json({'error':'任务文件不存在'},400); return
                old=JOBS.get(task_id)
                if old and old.get('status')=='运行中': self.send_json({'ok':True,'message':'任务已经在运行'}); return
                log_path=task_path.with_suffix('.log')
                py=os.environ.get('DOUYIN_PYTHON',sys.executable)
                log=log_path.open('a',encoding='utf-8')
                proc=subprocess.Popen([py,str(Path(__file__).with_name('download_task.py')),str(task_path)],stdout=log,stderr=subprocess.STDOUT,cwd=str(BASE))
                JOBS[task_id]={'task_id':task_id,'status':'运行中','pid':proc.pid,'log':str(log_path)}
                def watch():
                    code=proc.wait(); log.close(); JOBS[task_id].update(status='完成' if code==0 else '失败',returncode=code)
                threading.Thread(target=watch,daemon=True).start()
                self.send_json({'ok':True,'message':'下载任务已启动'}) ; return
            if u.path=='/api/review':
                c.execute('update similar set decision=?,note=? where a=? and b=?',(data.get('decision','暂不确定'),data.get('note',''),data.get('a'),data.get('b'))); c.commit(); self.send_json({'ok':True}); return
            self.send_json({'error':'未知操作'},404)
        finally: c.close()

if __name__=='__main__':
    print(f'本地抖音工作台: http://127.0.0.1:{PORT}')
    print(f'局域网地址: http://<本机IP>:{PORT}')
    print('管理密码已写入:', CONFIG)
    ThreadingHTTPServer((HOST,PORT),Handler).serve_forever()
