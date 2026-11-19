from __future__ import annotations
import base64, hashlib, json, os, secrets, shutil, sqlite3, threading, time, subprocess, sys
import re, unicodedata, socket
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
DOWNLOADER_PORT = int(os.environ.get('DOUYIN_DOWNLOADER_PORT', '8766'))

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

def process_alive(pid):
    try:
        pid = int(pid or 0)
        if pid <= 0:
            return False
        os.kill(pid, 0)
        return True
    except (OSError, ValueError, TypeError):
        return False

def primary_job_file(job_id):
    if not re.fullmatch(r'primaryindex_[A-Za-z0-9]+', str(job_id or '')):
        return None
    p = LIB / '索引' / '主库优先整理' / 'jobs' / f'{job_id}.json'
    return p if p.is_file() else None

def primary_runtime_state(job):
    """恢复服务重启前的扫描状态；只根据任务 JSON 和扫描器 PID 判断。"""
    if not isinstance(job, dict):
        return job
    if job.get('status') == '运行中':
        alive = process_alive(job.get('worker_pid'))
        job['worker_alive'] = alive
        if alive:
            job['needs_resume'] = False
        else:
            job['status'] = '已中断'
            job['needs_resume'] = True
    return job

def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute('''create table if not exists subscriptions(
        creator_id text primary key references creators(id), name text, homepage_url text,
        platform_id text, start_date text, mode text default 'since_date',
        status text default 'active', last_checked_at text, last_success_at text,
        created_at text, note text)''')
    c.execute('''create table if not exists player_follows(
        creator_id text primary key references creators(id), followed_at text not null)''')
    c.execute('create index if not exists metrics_work_metric_value on metrics(work_id,metric,value)')
    # Feed queries filter the same columns repeatedly. These indexes keep the
    # player responsive as the library grows beyond the primary archive.
    c.execute('create index if not exists files_feed_scope on files(kind,status,duplicate_of,library_role,creator_id)')
    c.execute('create index if not exists media_file_status on media(file_id,status)')
    c.execute("create index if not exists feedback_event_file on feedback(event_type,json_extract(extra_json,'$.file_id'))")
    c.execute("create index if not exists feedback_event_creator on feedback(event_type,json_extract(extra_json,'$.creator_id'))")
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

def lan_urls():
    ips=set()
    try:
        ips.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except Exception:
        pass
    try:
        s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.connect(('192.0.2.1',80)); ips.add(s.getsockname()[0]); s.close()
    except Exception:
        pass
    return [f'http://{ip}:{PORT}/' for ip in sorted(ips) if ip and not ip.startswith(('127.','169.254.'))]

INDEX = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>本地抖音工作台</title>
<style>body{margin:0;font:15px/1.5 system-ui,"Microsoft YaHei",sans-serif;background:#f5f6fa;color:#202535}header{background:#181b27;color:white;padding:14px 22px;display:flex;justify-content:space-between;align-items:center}main{max-width:1400px;margin:auto;padding:18px}.tabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}.tabs button,.tabs a,button{border:0;border-radius:8px;padding:9px 13px;cursor:pointer;background:#e6eaf3}.tabs button.active,.tabs a.primary,button.primary{background:#315bdc;color:#fff;text-decoration:none}.panel{background:#fff;border:1px solid #e0e4ed;border-radius:12px;padding:16px;margin-bottom:16px}.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}.stat{background:#f7f8fc;padding:12px;border-radius:9px}.stat b{display:block;font-size:24px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}.card{border:1px solid #e0e4ed;border-radius:10px;padding:12px;background:#fff}.card h3{margin:4px 0;font-size:16px}.muted{color:#6d7484}.danger{color:#b3261e}.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}input,select{padding:9px;border:1px solid #bcc5d6;border-radius:7px;font:inherit}.hidden{display:none}video{width:100%;max-height:260px;background:#111;border-radius:8px}.creatorbar{height:5px;background:#3c65d9;border-radius:4px;margin:-12px -12px 9px}.row{display:flex;justify-content:space-between;gap:8px;align-items:center}small{overflow-wrap:anywhere;color:#687187}.badge{font-size:12px;padding:3px 7px;border-radius:999px;background:#eef1f8}.dangerbtn{background:#b3261e;color:#fff}.service-status{align-self:center;font-size:12px;color:#cbd3df}.service-status[data-state="online"]{color:#79d6a4}.service-status[data-state="offline"]{color:#ffb4aa}.playerShell{max-width:560px;margin:auto;background:#111;color:#fff;border-radius:14px;overflow:hidden;position:relative}.playerShell.immersive{position:fixed;inset:0;z-index:9999;max-width:none;width:100vw;height:100vh;border-radius:0}.playerShell.immersive .feedViewport{height:100vh;min-height:0}.playerShell:fullscreen{max-width:none;width:100vw;height:100vh;border-radius:0}.playerShell:fullscreen .feedViewport{height:100vh;min-height:0}.playerShell.immersive .playerTop{position:absolute;inset:0 0 auto;z-index:5;background:linear-gradient(rgba(0,0,0,.38),transparent);pointer-events:none}.playerShell.immersive .playerTop select,.playerShell.immersive #modeButton,.playerShell.immersive #reason,.playerShell.immersive #modeNotice{display:none}.playerShell.immersive #fullscreenButton{display:block;margin-left:auto;pointer-events:auto;background:rgba(0,0,0,.45);color:#fff;font-size:22px;border-radius:50%;width:42px;height:42px;padding:0}.playerShell.immersive .videoOverlay h2,.playerShell.immersive .videoOverlay p,.playerShell.immersive .videoOverlay .why{display:none}.playerShell.immersive .videoOverlay{padding:18px;background:none}.playerShell.immersive .videoOverlay .actions{justify-content:flex-end}.playerShell:fullscreen .playerTop{position:absolute;inset:0 0 auto;z-index:5;background:linear-gradient(rgba(0,0,0,.38),transparent);pointer-events:none}.playerShell:fullscreen .playerTop select,.playerShell:fullscreen #modeButton,.playerShell:fullscreen #reason,.playerShell:fullscreen #modeNotice{display:none}.playerShell:fullscreen #fullscreenButton{display:block;margin-left:auto;pointer-events:auto;background:rgba(0,0,0,.45);color:#fff;font-size:22px;border-radius:50%;width:42px;height:42px;padding:0}.playerShell:fullscreen .videoOverlay h2,.playerShell:fullscreen .videoOverlay p,.playerShell:fullscreen .videoOverlay .why{display:none}.playerShell:fullscreen .videoOverlay{padding:18px;background:none}.playerShell:fullscreen .videoOverlay .actions{justify-content:flex-end}.playerTop{padding:12px;background:#181b27;display:flex;justify-content:space-between;align-items:center;gap:8px}.playerTop #reason{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.feedViewport{height:min(78vh,780px);min-height:520px;position:relative;background:#000;overflow:hidden;outline:none;touch-action:pan-y;user-select:none}.feedViewport>video{width:100%;height:100%;max-height:none;object-fit:contain;transition:opacity .16s ease}.videoOverlay{position:absolute;inset:auto 0 0;padding:70px 18px 18px;background:linear-gradient(transparent,rgba(0,0,0,.86));pointer-events:none}.videoOverlay h2,.videoOverlay p{margin:4px 0;text-shadow:0 1px 3px #000}.videoOverlay .actions{display:flex;gap:7px;flex-wrap:wrap;margin-top:12px;pointer-events:auto}.videoOverlay button{background:rgba(255,255,255,.9);color:#202535}.playerHelp{padding:10px;text-align:center;background:#181b27;color:#b9c1d4;font-size:12px}@keyframes heart{0%{opacity:0;transform:translate(-50%,-50%) scale(.5)}30%{opacity:1}100%{opacity:0;transform:translate(-50%,-50%) scale(1.25)}}@media(max-width:600px){body:has(.playerShell) header,body:has(.playerShell) main{padding:0}body:has(.playerShell) main{max-width:none}.playerShell{max-width:none;border-radius:0;height:calc(100vh - 52px)}.feedViewport{height:calc(100vh - 145px);min-height:420px}.playerShell .playerHelp{display:none}.playerTop{padding:9px 10px}}
</style>
<header><strong>本地抖音工作台</strong><span id="mode">管理模式（免密码）</span><span id="lanAddress">局域网访问地址：检测中…</span></header><main><div id="login" class="panel hidden"></div><div id="app"><nav class="tabs"><button data-tab="home" class="active">总览</button><button data-tab="creators">博主整理</button><button data-tab="primary">主库整理</button><button data-tab="sources">来源合并</button><button data-tab="subscriptions">自动更新</button><button data-tab="review">待确认</button><a class="primary" href="/player/">播放器</a><a class="tabs button" id="downloaderLink" href="#" target="_blank" rel="noopener">下载器</a><span id="downloaderStatus" class="service-status" data-state="checking">下载器：检测中</span></nav><section id="home" class="tab"></section><section id="creators" class="tab hidden"></section><section id="primary" class="tab hidden"></section><section id="sources" class="tab hidden"></section><section id="subscriptions" class="tab hidden"></section><section id="review" class="tab hidden"></section></div></main>
<script>
let token='',admin=true,cache={};
const $=x=>document.querySelector(x); const dl=document.querySelector('#downloaderLink'); if(dl) dl.href=location.protocol+'//'+location.hostname+':8766/'; const esc=x=>String(x??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
async function api(path,opt={}){opt.headers={...(opt.headers||{}),'Content-Type':'application/json',...(token?{'X-Admin-Token':token}:{})};let r=await fetch(path,opt);if(!r.ok)throw Error(await r.text());return r.json()}
async function refreshDownloaderStatus(){const el=$('#downloaderStatus');if(!el)return;try{const s=await api('/api/downloader-status');el.dataset.state=s.online?'online':'offline';el.textContent=s.online?'下载器：在线':'下载器：未启动';el.title=s.online?'本机下载器服务可连接':'启动统一工作台可启动下载器'}catch(e){el.dataset.state='offline';el.textContent='下载器：状态未知'}}
refreshDownloaderStatus();setInterval(refreshDownloaderStatus,15000);
function browse(){admin=false;$('#login').classList.add('hidden');$('#app').classList.remove('hidden');load()}
async function login(){try{let x=await api('/api/login',{method:'POST',body:JSON.stringify({password:$('#pw').value})});token=x.token;admin=true;$('#login').classList.add('hidden');$('#app').classList.remove('hidden');$('#mode').textContent='管理模式';load()}catch(e){$('#loginmsg').textContent='密码不正确'}}
document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{document.querySelectorAll('[data-tab]').forEach(x=>x.classList.remove('active'));b.classList.add('active');document.querySelectorAll('.tab').forEach(x=>x.classList.add('hidden'));$('#'+b.dataset.tab).classList.remove('hidden');render(b.dataset.tab)});
async function load(){cache.summary=await api('/api/summary');cache.creators=await api('/api/creators');cache.files=await api('/api/files?limit=100');cache.reviewFiles=await api('/api/review-files');cache.similar=await api('/api/similar');api('/api/lan-info').then(x=>{let el=$('#lanAddress');if(el)el.textContent='局域网访问地址：'+(x.urls||[]).join('、')}).catch(()=>{});render('home')}
function render(tab){if(tab==='home')renderHome();if(tab==='creators')renderCreators();if(tab==='primary')renderPrimary();if(tab==='sources')renderSources();if(tab==='subscriptions')renderSubscriptions();if(tab==='review')renderReview();if(tab==='player')renderPlayer()}
function renderPrimary(){let j=cache.primaryJob||{};if(!j.job_id&&!j._loaded){cache.primaryJob={_loaded:true};api('/api/primary-latest').then(x=>{cache.primaryJob=x;renderPrimary()}).catch(()=>{});return}let p=Number(j.percent||0);let running=j.status==='运行中';let report=j.summary&&j.summary.report;let execButton=j.status==='完成'&&report?'<button class="dangerbtn" id="primaryApply">执行整理</button>':'';$('#primary').innerHTML='<div class="panel"><h2>主库优先整理</h2><p>以“关注归档”为主库，扫描历史补充来源，重复项先进入虚拟回收站，主库没有的标记为“疑似绝版，待核对”。扫描阶段不会移动文件。</p><button class="primary" id="primaryStart">'+(running?'继续查看扫描':'开始扫描')+'</button> '+execButton+' <button class="dangerbtn" id="recyclePurge">清空虚拟回收站（包括索引）</button><div style="margin-top:16px;background:#e8edf5;border-radius:8px;height:18px;overflow:hidden"><div style="width:'+p+'%;height:100%;background:#315bdc;transition:width .3s"></div></div><p>状态：'+esc(j.status||'未开始')+' · 阶段：'+esc(j.phase||'未开始')+' · '+esc(j.processed||0)+' / '+esc(j.total||0)+' · '+p+'%</p><small>'+esc(j.current||'')+'</small><pre style="white-space:pre-wrap">'+esc(j.summary?JSON.stringify(j.summary,null,2):'')+'</pre></div><div id="primaryIndexPanel"></div>';$('#primaryStart').onclick=async()=>{if(running){pollPrimary(j.job_id);return}let r=await api('/api/primary-start',{method:'POST',body:'{}'});cache.primaryJob={job_id:r.job_id,status:'运行中'};renderPrimary();pollPrimary(r.job_id)};let ab=$('#primaryApply');if(ab)ab.onclick=async()=>{if(!confirm('确认执行整理？重复文件将移动到虚拟回收站，疑似绝版将写入索引。'))return;let r=await api('/api/primary-apply',{method:'POST',body:JSON.stringify({report})});cache.primaryJob={job_id:r.job_id,status:'运行中'};renderPrimary();pollPrimary(r.job_id)};let rb=$('#recyclePurge');if(rb)rb.onclick=async()=>{if(!confirm('确认永久删除虚拟回收站内文件并移除对应索引？此操作不可撤销。'))return;let r=await api('/api/recycle-purge',{method:'POST',body:'{}'});cache.primaryJob={job_id:r.job_id,status:'运行中'};renderPrimary();pollPrimary(r.job_id)};if(j.job_id&&!['完成','失败'].includes(j.status))pollPrimary(j.job_id);renderPrimaryIndex()}
function renderPrimaryIndex(){let j=cache.primaryIndexJob||{};if(!j._loaded&&!j.job_id){cache.primaryIndexJob={_loaded:true};api('/api/primary-index-latest').then(x=>{cache.primaryIndexJob=x;renderPrimaryIndex();if(x.job_id&&x.status==='运行中'&&!x.needs_resume)pollPrimaryIndex(x.job_id)}).catch(()=>{});return}let p=Number(j.percent||0),running=j.status==='运行中'&&!j.needs_resume;let label=running?'扫描运行中…':j.job_id?'继续扫描':'开始扫描';let stop=running?'<button id="primaryIndexStop">停止扫描</button>':'';let heartbeat=j.updated_at?' · 最近更新 '+esc(j.updated_at):'';let summary=j.summary?'<pre style="white-space:pre-wrap">'+esc(JSON.stringify(j.summary,null,2))+'</pre>':'';$('#primaryIndexPanel').innerHTML='<div class="panel"><h2>扫描并同步主库索引</h2><p>将“关注归档”中的 MP4 和主页作品元数据登记到播放器索引。扫描可暂停后继续，已有记录会自动跳过；不会移动或删除视频。</p><button class="primary" id="primaryIndexStart" '+(running?'disabled':'')+'>'+label+'</button> '+stop+'<div style="margin-top:14px;background:#e8edf5;border-radius:8px;height:16px;overflow:hidden"><div style="width:'+p+'%;height:100%;background:#138a68;transition:width .3s"></div></div><p>状态：'+esc(j.status||'未开始')+' · '+esc(j.phase||'未开始')+' · '+esc(j.processed||0)+' / '+esc(j.total||0)+' · '+p+'% · 新增 '+esc(j.added||j.summary?.added||0)+' 个'+heartbeat+'</p><small>'+esc(j.current||j.error||'')+(j.stale_count?' · 有 '+j.stale_count+' 个旧文件校验信息不一致，已跳过':'')+'</small>'+summary+'</div><div class="panel" id="primaryAuditPanel"><p class="muted">等待索引核对…</p></div>';$('#primaryIndexStart').onclick=async()=>{if(running)return;let r=await api('/api/primary-index-start',{method:'POST',body:'{}'});cache.primaryIndexJob={job_id:r.job_id,status:'运行中'};renderPrimaryIndex();pollPrimaryIndex(r.job_id)};let sb=$('#primaryIndexStop');if(sb)sb.onclick=async()=>{await api('/api/primary-index-stop',{method:'POST',body:JSON.stringify({job_id:j.job_id})});pollPrimaryIndex(j.job_id)};if(!running)renderIndexAudit()}
async function renderIndexAudit(){let box=$('#primaryAuditPanel');if(!box)return;box.innerHTML='<p class="muted">正在核对主库索引…</p>';try{let a=await api('/api/primary-index-audit');cache.primaryAudit=a;let restore=a.mtime_restore_candidates||0;let missing=a.missing_path_count||0;let sameHash=a.missing_with_existing_hash_count||0;let noCopy=a.missing_without_existing_hash_count||0;let present=a.indexed_present_count??(a.indexed_mp4_count-missing);box.innerHTML='<h3>索引覆盖与异常核对</h3><div class="stats"><div class="stat"><span>磁盘 MP4</span><b>'+a.disk_mp4_count+'</b></div><div class="stat"><span>可用索引 MP4</span><b>'+present+'</b></div><div class="stat"><span>索引记录总数</span><b>'+a.indexed_mp4_count+'</b></div><div class="stat"><span>漏登记</span><b>'+a.unindexed_count+'</b></div><div class="stat"><span>时间变化候选</span><b>'+a.stale_time_only_count+'</b></div><div class="stat"><span>路径不存在</span><b>'+missing+'</b></div><div class="stat"><span>同哈希可核对</span><b>'+sameHash+'</b></div><div class="stat"><span>无现存副本</span><b>'+noCopy+'</b></div><div class="stat"><span>可预览旧时间</span><b>'+restore+'</b></div></div><p class="muted">“索引记录总数”包含已不存在的历史路径；“可用索引 MP4”只统计当前仍在磁盘上的记录。此页只读，不修改数据库和视频。旧时间恢复仍需确认文件哈希，并保存当前时间以便撤回。</p><details><summary>查看异常分类</summary><pre style="white-space:pre-wrap;max-height:320px;overflow:auto">'+esc(JSON.stringify({time_changed:a.stale_examples,missing_paths:a.missing_examples,unindexed:a.unindexed_examples},null,2))+'</pre></details>';attachAuditExport()}catch(e){box.innerHTML='<p class="danger">核对失败：'+esc(e.message||e)+'</p>'}}
function attachAuditExport(){const box=$('#primaryAuditPanel');if(!box||!cache.primaryAudit||$('#downloadAuditPlan'))return;const b=document.createElement('button');b.id='downloadAuditPlan';b.textContent='下载核对清单';b.title='导出只读核对结果，不修改视频';b.onclick=()=>{const report={created_at:new Date().toISOString(),root:cache.primaryAudit.root,read_only:true,mtime_restore_requires_hash_check:true,mtime_candidates:cache.primaryAudit.stale_examples||[],missing_paths:cache.primaryAudit.missing_examples||[],unindexed:cache.primaryAudit.unindexed_examples||[]};const blob=new Blob([JSON.stringify(report,null,2)],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='主库索引核对_'+new Date().toISOString().replace(/[:.]/g,'-')+'.json';a.click();URL.revokeObjectURL(a.href)};box.prepend(b);const plan=document.createElement('button');plan.id='createMtimePlan';plan.textContent='生成时间恢复计划';plan.title='只生成计划，不修改文件';plan.onclick=async()=>{plan.disabled=true;plan.textContent='生成中…';try{const x=await api('/api/primary-mtime-plan',{method:'POST',body:'{}'});cache.primaryMtimePlan=x;const note=document.createElement('p');note.className='muted';note.textContent='已生成 '+x.candidate_count+' 个候选，计划编号：'+x.plan_id;box.insertBefore(note,box.firstChild)}catch(e){alert('生成计划失败：'+(e.message||e))}finally{plan.disabled=false;plan.textContent='生成时间恢复计划'}};box.prepend(plan)}
async function pollPrimaryIndex(id){let r=await api('/api/primary-index-job?job_id='+encodeURIComponent(id));cache.primaryIndexJob=r;renderPrimaryIndex();if(r.status==='运行中'&&!r.needs_resume)setTimeout(()=>pollPrimaryIndex(id),1000)}
async function pollPrimary(id){let r=await api('/api/primary-job?job_id='+encodeURIComponent(id));cache.primaryJob=r;renderPrimary();if(!['完成','失败'].includes(r.status))setTimeout(()=>pollPrimary(id),1000)}
function renderSources(){let j=cache.sourceJob||{};let out=j.output||j.message||'';let action='';let batch='';if(j.status==='完成'&&out.includes('batch_id')){let m=out.match(/"batch_id"\\s*:\\s*"([^"]+)/);if(m){batch=m[1];action='<button class="primary" id="sourceImportButton">只复制确定不重复项</button> <button class="warn" id="sourceImportSuspectedButton">复核后连同疑似项复制</button><p class="muted">疑似同作品不会默认复制；只有确认来源确实是不同副本或需要保留时，才使用第二个按钮。</p>'}}$('#sources').innerHTML='<div class="panel"><h2>来源合并</h2><p>输入其他视频目录，先扫描查重，再确认复制到主库待整理区。原目录不会删除。</p><div class="toolbar"><input id="sourcePath" style="min-width:460px" placeholder="例如 E:\\抖音企划\\历史视频"><button class="primary" id="sourceScan">扫描来源</button></div><div id="sourceResult"><p class="muted">状态：'+esc(j.status||'未开始')+'</p><pre style="white-space:pre-wrap">'+esc(out)+'</pre>'+action+'</div></div>';$('#sourceScan').onclick=async()=>{let source=$('#sourcePath').value.trim();if(!source)return alert('请填写来源目录');let r=await api('/api/source-scan',{method:'POST',body:JSON.stringify({source})});cache.sourceJob={job_id:r.job_id,message:'正在扫描…'};renderSources();pollSource(r.job_id)};let ib=$('#sourceImportButton');if(ib)ib.onclick=()=>importSource(batch,false);let isb=$('#sourceImportSuspectedButton');if(isb)isb.onclick=()=>importSource(batch,true);if(j.job_id&&!['完成','失败'].includes(j.status))pollSource(j.job_id)}
async function importSource(batch,acceptSuspected=false){if(!confirm(acceptSuspected?'确认已人工复核疑似同作品项，并复制到待整理区？':'确认只复制确定不重复视频到主库待整理区？'))return;let r=await api('/api/source-import',{method:'POST',body:JSON.stringify({batch,accept_suspected:acceptSuspected})});cache.sourceJob={job_id:r.job_id,message:'正在复制…'};renderSources();pollSource(r.job_id)}
async function pollSource(id){let r=await api('/api/source-job?job_id='+encodeURIComponent(id));cache.sourceJob=r;renderSources();if(!['完成','失败'].includes(r.status))setTimeout(()=>pollSource(id),1200)}
function renderHome(){let s=cache.summary;$('#home').innerHTML='<div class="panel"><h2>工作台总览</h2><div class="stats">'+[['可用文件',s.available],['博主身份',s.creators],['作品',s.works],['重复副本',s.duplicates],['相似候选',s.similar],['待确认文件',s.unknown]].map(x=>'<div class="stat">'+x[0]+'<b>'+x[1]+'</b></div>').join('')+'</div></div><div class="panel"><h3>建议顺序</h3><p>先处理博主身份和相似候选，再进入播放器。所有管理动作都要在管理模式确认。</p><button onclick="go(&quot;creators&quot;)">开始博主整理</button> <button onclick="go(&quot;review&quot;)">查看待确认</button></div>'}
function go(t){document.querySelector('[data-tab="'+t+'"]').click()}
function renderCreators(){let q=cache.creatorQ||'';let mergedIds=new Set();cache.creators.forEach(y=>{try{JSON.parse(y.aliases_json||'[]').forEach(a=>{if(a&&a.id)mergedIds.add(a.id)})}catch(e){}});let list=cache.creators.filter(x=>!mergedIds.has(x.id)&&!String(x.identity_status||'').startsWith('已合并到')).filter(x=>!q||(x.name||'').toLowerCase().includes(q.toLowerCase())||x.id.toLowerCase().includes(q.toLowerCase())||String(x.aliases_json||'').toLowerCase().includes(q.toLowerCase()));$('#creators').innerHTML='<div class="panel"><h2>博主整理</h2><div class="toolbar"><input id="cq" value="'+esc(q)+'" placeholder="搜索博主名、历史昵称或编号"><button id="creatorSearchButton">搜索</button><button id="candidateButton">名称合并候选</button><span class="muted">合并后统一显示，历史名称保留在卡片中</span></div><div id="candidatePanel"></div><div class="grid">'+list.map(x=>{let aliases=[];try{aliases=JSON.parse(x.aliases_json||'[]')}catch(e){};let aliasText=aliases.map(a=>typeof a==='string'?a:a.name).filter(Boolean).filter(n=>n!==x.name);return '<article class="card"><div class="creatorbar"></div><h3>'+esc(x.name||'博主待确认')+'</h3>'+(aliasText.length?'<p class="badge">已合并：'+esc(aliasText.join('、'))+'</p>':'')+'<p>文件 '+x.file_count+' · 视频 '+x.video_count+' · 图片 '+x.image_count+'</p><small>'+esc(x.id)+'<br>'+esc(x.identity_status||'')+'</small>'+(admin?'<p><button class="editCreatorButton" data-id="'+esc(x.id)+'">修改归类</button></p>':'')+'</article>'}).join('')+'</div></div>';$('#creatorSearchButton').onclick=creatorSearch;$('#candidateButton').onclick=showCandidates;document.querySelectorAll('.editCreatorButton').forEach(b=>b.onclick=()=>editCreator(b.dataset.id))}
async function showCandidates(){let x=await api('/api/creator-candidates');let cards=x.map(g=>{let people=g.items.map(i=>esc(i.name)+' · '+i.file_count+' 个文件 · '+esc(i.id)).join('<br>');let action=admin?'<button class="mergeButton" data-a="'+g.items[0].id+'" data-b="'+g.items[1].id+'">合并到第一个身份</button>':'';return '<div class="card"><b>'+esc(g.normalized_name)+'</b><p>'+people+'</p>'+action+' <button class="keepButton">保持分开</button></div>'}).join('');$('#candidatePanel').innerHTML='<div class="panel"><h3>名称合并候选</h3>'+(cards||'<p>暂无规范化名称相同的候选。</p>')+'</div>';document.querySelectorAll('.mergeButton').forEach(b=>b.onclick=()=>mergeCreators(b.dataset.a,b.dataset.b));document.querySelectorAll('.keepButton').forEach(b=>b.onclick=e=>e.target.closest('.card').remove())}
async function mergeCreators(a,b){if(!confirm('确认合并这两个博主身份？原始记录会保留为别名。'))return;await api('/api/creator-merge',{method:'POST',body:JSON.stringify({primary:a,secondary:b})});cache.creators=await api('/api/creators');renderCreators();alert('已合并，原身份已保留为别名记录')}
function creatorSearch(){cache.creatorQ=$('#cq').value;renderCreators()}
async function renderSubscriptions(){let x=await api('/api/subscriptions');let options=cache.creators.map(c=>'<option value="'+esc(c.id)+'">'+esc(c.name||'博主待确认')+' · '+esc(c.id)+'</option>').join('');$('#subscriptions').innerHTML='<div class="panel"><h2>博主自动更新</h2><p>选择博主和起始日期，生成给下载工具使用的任务文件。下载完成后请放入待导入文件夹。</p><div class="toolbar"><select id="subCreator">'+options+'</select><input id="subUrl" placeholder="博主主页链接（可选）"><input id="subDate" type="date"><button id="addSub">加入订阅</button></div><div id="subList" class="grid">'+x.map(s=>'<article class="card"><h3>'+esc(s.name)+'</h3><p>起始日期：'+esc(s.start_date||'未设置')+' · 状态：'+esc(s.status)+'</p><small>'+esc(s.homepage_url||'未填写主页链接')+'<br>上次检查：'+esc(s.last_checked_at||'尚未检查')+'</small><p><button class="taskButton" data-id="'+esc(s.creator_id)+'">生成下载任务</button></p></article>').join('')+'</div></div><div id="taskResult"></div>';$('#addSub').onclick=addSubscription;document.querySelectorAll('.taskButton').forEach(b=>b.onclick=()=>exportTask(b.dataset.id))}
async function addSubscription(){if(!admin){alert('请先进入管理模式');return}let r=await api('/api/subscriptions',{method:'POST',body:JSON.stringify({creator_id:$('#subCreator').value,homepage_url:$('#subUrl').value,start_date:$('#subDate').value})});renderSubscriptions();alert(r.message||'已加入订阅')}
async function exportTask(id){if(!admin){alert('请先进入管理模式');return}let r=await api('/api/export-task',{method:'POST',body:JSON.stringify({creator_id:id})});$('#taskResult').innerHTML='<div class="panel"><h3>任务已生成</h3><p>任务编号：'+esc(r.task_id)+'</p><p>CSV：'+esc(r.csv)+'<br>JSON：'+esc(r.json)+'<br>下载目录：'+esc(r.download_dir)+'</p><button class="primary" id="runTask">立即开始下载</button><span id="taskStatus" class="muted"></span></div>';$('#runTask').onclick=()=>runTask(r.task_id)}
async function runTask(id){if(!admin)return;let r=await api('/api/run-task',{method:'POST',body:JSON.stringify({task_id:id})});$('#taskStatus').textContent=r.message||'已启动';let timer=setInterval(async()=>{let s=await api('/api/task-status?task_id='+encodeURIComponent(id));$('#taskStatus').textContent=' · '+(s.status||'运行中');if(['完成','失败'].includes(s.status))clearInterval(timer)},1500)}
async function editCreator(id){let name=prompt('输入新的博主名；留空取消');if(!name)return;await api('/api/creator/'+id,{method:'POST',body:JSON.stringify({name})});cache.creators=await api('/api/creators');renderCreators()}
function renderReview(){let files=cache.reviewFiles||[];let groups={};files.forEach(x=>(groups[x.review_reason]??=[]).push(x));let cards=Object.entries(groups).map(([reason,items])=>'<div class="panel"><h3>'+esc(reason)+'（'+items.length+'）</h3><div class="grid">'+items.slice(0,100).map(x=>'<article class="card"><h3>'+esc(x.title||x.name||'未命名')+'</h3><p>'+esc(x.creator||'博主待确认')+' · '+esc(x.media_status||x.status||'')+'</p><small>'+esc(x.current_path)+'</small></article>').join('')+'</div>'+(items.length>100?'<p class="muted">还有 '+(items.length-100)+' 条，请使用搜索或导出清单。</p>':'')+'</div>').join('');let similarCards=(cache.similar||[]).map(x=>'<div class="card"><div style="display:grid;grid-template-columns:1fr 1fr;gap:12px"><article><video controls preload="metadata" style="width:100%;max-height:260px;background:#111" src="/media?path='+encodeURIComponent(x.path_a||'')+'"></video><h3>A：'+esc(x.title_a||'未命名')+'</h3><p>'+esc(x.creator_a||'博主待确认')+'</p><small>'+esc(x.path_a||'路径不存在')+'</small></article><article><video controls preload="metadata" style="width:100%;max-height:260px;background:#111" src="/media?path='+encodeURIComponent(x.path_b||'')+'"></video><h3>B：'+esc(x.title_b||'未命名')+'</h3><p>'+esc(x.creator_b||'博主待确认')+'</p><small>'+esc(x.path_b||'路径不存在')+'</small></article></div><p>'+esc(x.reason)+' · 参考值 '+x.score+' · 当前：'+esc(x.decision||'待确认')+'</p><button class="similarReview" data-a="'+esc(x.a)+'" data-b="'+esc(x.b)+'" data-decision="同一作品的不同版本">同一作品版本</button> <button class="similarReview" data-a="'+esc(x.a)+'" data-b="'+esc(x.b)+'" data-decision="不同作品">不同作品</button> <button class="similarReview" data-a="'+esc(x.a)+'" data-b="'+esc(x.b)+'" data-decision="暂不确定">暂不确定</button></div>').join('');$('#review').innerHTML='<div class="panel"><h2>待确认与异常</h2><p>这里只显示确实需要补资料、核对或检查的记录，共 '+files.length+' 条。</p></div>'+cards+'<div class="panel"><h2>相似候选</h2><p>左右播放抽样视频进行对比，共 '+similarCards.length+' 组。</p>'+similarCards+'</div>';document.querySelectorAll('.similarReview').forEach(b=>b.onclick=()=>review(b.dataset.a,b.dataset.b,b.dataset.decision))}
async function review(a,b,decision){if(!admin){alert('请先进入管理模式');return}await api('/api/review',{method:'POST',body:JSON.stringify({a,b,decision})});cache.similar=await api('/api/similar');renderReview()}
load().catch(()=>{});
</script></html>'''

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass
    def send_json(self, obj, status=200):
        data=json.dumps(obj,ensure_ascii=False).encode(); self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
    def body(self):
        n=int(self.headers.get('Content-Length','0')); return json.loads(self.rfile.read(n) or b'{}')
    def admin(self): return True
    def do_GET(self):
        u=urlparse(self.path)
        if u.path=='/api/lan-info':
            self.send_json({'host':socket.gethostname(),'urls':lan_urls()}); return
        if u.path=='/api/downloader-status':
            try:
                with socket.create_connection(('127.0.0.1',DOWNLOADER_PORT),timeout=0.5):
                    online=True
            except OSError:
                online=False
            self.send_json({'online':online,'port':DOWNLOADER_PORT}); return
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
        if u.path=='/api/source-job':
            job_id=parse_qs(u.query).get('job_id',[''])[0]
            self.send_json(JOBS.get(job_id, {'job_id':job_id,'status':'未启动'})); return
        if u.path=='/api/primary-job':
            job_id=parse_qs(u.query).get('job_id',[''])[0]; job=JOBS.get(job_id,{})
            pp=Path(job.get('progress','')) if job.get('progress') else None
            if pp and pp.exists():
                try: job.update(json.loads(pp.read_text(encoding='utf-8')))
                except Exception: pass
            self.send_json(job or {'job_id':job_id,'status':'未启动'}); return
        if u.path=='/api/primary-latest':
            jobs_dir=LIB/'索引'/'主库优先整理'/'jobs'; latest=None
            if jobs_dir.exists():
                files=sorted(jobs_dir.glob('primaryjob_*.json'),key=lambda p:p.stat().st_mtime,reverse=True)
                if files:
                    latest=json.loads(files[0].read_text(encoding='utf-8')); latest['job_id']=files[0].stem
            self.send_json(latest or {'status':'未开始'}); return
        if u.path=='/api/primary-index-job':
            job_id=parse_qs(u.query).get('job_id',[''])[0]; live=JOBS.get(job_id,{}); job=dict(live)
            if not job:
                pp=primary_job_file(job_id)
                if pp:
                    job={'job_id':job_id,'kind':'primary-index','progress':str(pp),'stop_file':str(pp.with_suffix(pp.suffix+'.stop'))}
            pp=Path(job.get('progress','')) if job.get('progress') else None
            if pp and pp.exists():
                try: job.update(json.loads(pp.read_text(encoding='utf-8')))
                except Exception: pass
            job=primary_runtime_state(job)
            self.send_json(job or {'job_id':job_id,'status':'未启动'}); return
        if u.path=='/api/primary-index-latest':
            jobs_dir=LIB/'索引'/'主库优先整理'/'jobs'; latest=None
            if jobs_dir.exists():
                files=sorted(jobs_dir.glob('primaryindex_*.json'),key=lambda p:p.stat().st_mtime,reverse=True)
                if files:
                    latest=json.loads(files[0].read_text(encoding='utf-8')); latest['job_id']=files[0].stem
                    latest=primary_runtime_state(latest)
            self.send_json(latest or {'status':'未开始'}); return
        if u.path=='/api/primary-index-audit':
            # 只读审计：不写 SQLite、不改文件，用于解释时间变化和孤立路径。
            root=LIB/'关注归档'
            if not root.is_dir(): self.send_json({'error':'主库目录不存在'},404); return
            try:
                audit_db=sqlite3.connect(f'file:{DB.as_posix()}?mode=ro', uri=True)
                audit_db.row_factory=sqlite3.Row
                indexed={r['current_path']:dict(r) for r in audit_db.execute(
                    "select id,current_path,size,mtime_ns,sha256,status,library_role,work_id,creator_id from files where current_path like ? and lower(current_path) like '%.mp4'", (str(root)+'%',))}
            finally:
                try: audit_db.close()
                except Exception: pass
            disk_paths={str(x):x for x in root.rglob('*.mp4') if x.is_file()}
            missing=[]; stale=[]; unindexed=[]
            for path in disk_paths.values():
                row=indexed.get(str(path))
                if not row:
                    unindexed.append(str(path)); continue
                try: stat=path.stat()
                except OSError: continue
                if row.get('size')!=stat.st_size or row.get('mtime_ns')!=stat.st_mtime_ns:
                    stale.append({'file_id':row['id'],'path':str(path),'indexed_mtime_ns':row.get('mtime_ns'),'current_mtime_ns':stat.st_mtime_ns,'indexed_size':row.get('size'),'current_size':stat.st_size,'time_only':row.get('size')==stat.st_size,'sha256':row.get('sha256')})
            existing_hashes={}
            for path,row in indexed.items():
                if path in disk_paths and row.get('sha256'):
                    existing_hashes.setdefault(row['sha256'],[]).append(path)
            for path,row in indexed.items():
                if path not in disk_paths:
                    peers=existing_hashes.get(row.get('sha256'),[])
                    missing.append({'file_id':row['id'],'path':path,'sha256':row.get('sha256'),'work_id':row.get('work_id'),'creator_id':row.get('creator_id'),'same_hash_paths':peers[:8],'same_hash_count':len(peers)})
            same_hash_missing=sum(1 for x in missing if x['same_hash_count'])
            self.send_json({'root':str(root),'disk_mp4_count':len(disk_paths),'indexed_mp4_count':len(indexed),'indexed_present_count':len(indexed)-len(missing),'unindexed_count':len(unindexed),'stale_count':len(stale),'stale_time_only_count':sum(1 for x in stale if x['time_only']),'missing_path_count':len(missing),'missing_with_existing_hash_count':same_hash_missing,'missing_without_existing_hash_count':len(missing)-same_hash_missing,'mtime_restore_candidates':sum(1 for x in stale if x['time_only']),'unindexed_examples':unindexed[:20],'stale_examples':stale[:100],'missing_examples':missing[:100],'read_only':True}); return
        c=db()
        try:
            if u.path=='/api/summary':
                x=c.execute("select count(*) n from files where status!='已永久删除'").fetchone()['n']; self.send_json({'available':x,'creators':c.execute('select count(*) n from creators').fetchone()['n'],'works':c.execute('select count(*) n from works').fetchone()['n'],'duplicates':c.execute("select count(*) n from files where duplicate_of is not null and status!='已永久删除'").fetchone()['n'],'similar':c.execute('select count(*) n from similar').fetchone()['n'],'unknown':c.execute("select count(*) n from files where creator_id is null and status!='已永久删除'").fetchone()['n']}); return
            if u.path=='/api/creators': self.send_json(rows(c,"select c.id,c.name,c.platform_id,c.identity_status,coalesce(c.aliases_json,'[]') aliases_json,count(f.id) file_count,sum(case when f.kind='video' then 1 else 0 end) video_count,sum(case when f.kind='image' then 1 else 0 end) image_count from creators c left join files f on f.creator_id=c.id and f.status!='已永久删除' group by c.id order by case when c.identity_status like '已合并到%' then 1 else 0 end,file_count desc,c.name")); return
            if u.path=='/api/following':
                query=parse_qs(u.query); term=query.get('q',[''])[0].strip(); args=[]; where=''
                if term:
                    where='where c.name like ? or c.id like ? or c.platform_id like ? or c.aliases_json like ?'
                    pattern='%'+term+'%'; args=[pattern,pattern,pattern,pattern]
                elif query.get('following',['0'])[0]=='1': where='where pf.creator_id is not null'
                self.send_json(rows(c,f'''select c.id creator_id,c.name,c.platform_id,c.aliases_json,
                    count(distinct case when f.kind='video' and f.status!='已永久删除' and f.duplicate_of is null then f.work_id end) video_count,
                    max(pf.followed_at) followed_at,case when pf.creator_id is null then 0 else 1 end is_following
                    from creators c left join files f on f.creator_id=c.id left join player_follows pf on pf.creator_id=c.id
                    {where} group by c.id order by is_following desc,video_count desc,c.name limit 200''',args)); return
            if u.path=='/api/creator-videos':
                query=parse_qs(u.query); cid=query.get('creator_id',[''])[0]; page=max(0,int(query.get('page',['0'])[0] or 0)); limit=min(60,max(12,int(query.get('limit',['36'])[0] or 36)))
                self.send_json(rows(c,'''with likes as (select work_id,max(value) like_count from metrics where metric='点赞量' group by work_id)
                    select f.id file_id,f.work_id,f.creator_id,f.current_path,f.library_role,f.video_label,
                    w.title,coalesce(cr.name,'博主待确认') creator,m.duration,l.like_count,
                    case when l.like_count is not null then '历史点赞量' else '无点赞记录' end like_source
                    from files f join works w on w.id=f.work_id left join creators cr on cr.id=f.creator_id left join media m on m.file_id=f.id
                    left join likes l on l.work_id=f.work_id
                    where f.creator_id=? and f.kind='video' and f.status!='已永久删除' and f.duplicate_of is null
                    order by like_count is null,like_count desc,w.published_at desc,f.id limit ? offset ?''',(cid,limit,page*limit))); return
            if u.path=='/api/creator-candidates':
                items=rows(c,"select c.id,c.name,count(f.id) file_count from creators c left join files f on f.creator_id=c.id and f.status!='已永久删除' group by c.id")
                groups={}
                for x in items: groups.setdefault(normalize_creator(x['name']),[]).append(x)
                self.send_json([{'normalized_name':k,'items':v} for k,v in groups.items() if k and len(v)>1]); return
            if u.path=='/api/subscriptions':
                self.send_json(rows(c,"select s.*,coalesce(s.name,cr.name,'博主待确认') name from subscriptions s left join creators cr on cr.id=s.creator_id order by s.status desc,s.name")); return
            if u.path=='/api/files': self.send_json(rows(c,"select f.id,f.current_path,f.status,f.kind,f.creator_id,f.work_id,f.duplicate_of,f.video_label,w.title,coalesce(cr.name,'博主待确认') creator,m.status media_status,m.duration from files f left join works w on w.id=f.work_id left join creators cr on cr.id=f.creator_id left join media m on m.file_id=f.id where f.status!='已永久删除' order by f.id limit 100")); return
            if u.path=='/api/review-files': self.send_json(rows(c,"""select f.id,f.current_path,f.status,f.kind,f.creator_id,f.work_id,f.duplicate_of,f.video_label,w.title,coalesce(cr.name,'博主待确认') creator,m.status media_status,m.duration,
                case when f.creator_id is null then '博主未确认' when f.kind='image' and f.work_id is null then '图片未关联作品' when m.status in ('下载未完成','图片异常','无法抽帧','检查失败','解码有告警','仅音频，扩展名为MP4') then '媒体检查异常' when f.video_label='疑似绝版，待核对' then '疑似绝版，待核对' else '待补资料' end review_reason
                from files f left join works w on w.id=f.work_id left join creators cr on cr.id=f.creator_id left join media m on m.file_id=f.id
                where f.status!='已永久删除' and (f.creator_id is null or (f.kind='image' and f.work_id is null) or m.status in ('下载未完成','图片异常','无法抽帧','检查失败','解码有告警','仅音频，扩展名为MP4') or f.video_label='疑似绝版，待核对') order by f.id desc limit 20000""")); return
            if u.path in ('/api/favorites','/api/collections'):
                kind=parse_qs(u.query).get('type',['favorite'])[0] if u.path=='/api/collections' else 'favorite'
                if kind not in ('like','favorite'): kind='favorite'
                off='unlike' if kind=='like' else 'unfavorite'
                self.send_json(rows(c,f"""with active as (
                    select json_extract(a.extra_json,'$.file_id') file_id,max(a.rowid) event_rowid
                    from feedback a where a.event_type=? and json_extract(a.extra_json,'$.file_id') is not null
                    group by json_extract(a.extra_json,'$.file_id')),
                    preferred as (select f.*,row_number() over(partition by f.work_id order by case when coalesce(f.library_role,'')='主库' then 0 else 1 end,f.current_path) rn
                      from files f where f.status!='已永久删除' and f.kind='video' and f.duplicate_of is null)
                    select f.id file_id,f.work_id,f.creator_id,f.current_path,f.library_role,f.video_label,w.title,
                      coalesce(cr.name,'博主待确认') creator,m.duration
                    from active a join preferred f on f.id=a.file_id and f.rn=1 left join works w on w.id=f.work_id
                      left join creators cr on cr.id=f.creator_id left join media m on m.file_id=f.id
                    where 1=1
                      and not exists(select 1 from feedback b where b.event_type=? and json_extract(b.extra_json,'$.file_id')=f.id and b.rowid>a.event_rowid)
                    order by a.event_rowid desc limit 5000""",(kind,off))); return
            if u.path=='/api/history-status':
                imports=rows(c,'select * from history_imports order by imported_at desc') if c.execute("select count(*) from sqlite_master where type='table' and name='history_imports'").fetchone()[0] else []
                self.send_json({'imports':imports,'matched':sum(x['matched_count'] for x in imports),'unmatched_deleted':sum(x['unmatched_count'] for x in imports)}); return
            if u.path=='/api/feed':
                query=parse_qs(u.query); mode=query.get('mode',['recommend'])[0]; source=query.get('source',['primary'])[0]; creator_id=query.get('creator_id',[''])[0]
                page=max(0,int(parse_qs(u.query).get('page',['0'])[0] or 0))
                limit=min(30,max(1,int(parse_qs(u.query).get('limit',['12'])[0] or 12)))
                order={'random':'random()','creator':'f.creator_id,random()','recommend':'''case when coalesce(f.library_role,'补充来源')='主库' then 0 else 1 end,
                    ((select count(*) from feedback lb
                    where lb.event_type='like' and json_extract(lb.extra_json,'$.creator_id')=coalesce(f.creator_id,'')) * 1000
                    + abs(random()) % 100000) desc'''} .get(mode,'random()')
                source_sql={'primary':" and coalesce(f.library_role,'主库')='主库' and coalesce(f.video_label,'')!='疑似绝版，待核对'",'supplement':" and coalesce(f.library_role,'')='补充来源' and coalesce(f.video_label,'')!='疑似绝版，待核对'",'rare':" and f.video_label='疑似绝版，待核对'",'all':'','favorites':" and exists(select 1 from feedback fav where fav.event_type='favorite' and json_extract(fav.extra_json,'$.file_id')=f.id and not exists(select 1 from feedback unf where unf.event_type='unfavorite' and json_extract(unf.extra_json,'$.file_id')=f.id and unf.rowid>fav.rowid))"}.get(source,'')
                creator_sql=' and f.creator_id=?' if creator_id else ''
                sql=f'''select f.id file_id,f.work_id,f.creator_id,f.current_path,f.status,f.library_role,f.video_label,w.title,coalesce(cr.name,'博主待确认') creator,m.duration,
                    case when ?='creator' then '博主轮播' when ?='random' then '随机探索' else '本地推荐' end reason
                    from files f left join works w on w.id=f.work_id left join creators cr on cr.id=f.creator_id left join media m on m.file_id=f.id
                    where f.kind='video' and f.status!='已永久删除' and f.duplicate_of is null
                    and coalesce(m.status,'') not in ('仅音频，扩展名为MP4','下载未完成','无法抽帧','检查失败')
                    and not exists(select 1 from feedback b where b.event_type='block_video' and json_extract(b.extra_json,'$.file_id')=f.id)
                    and not exists(select 1 from feedback b where b.event_type='block_creator' and json_extract(b.extra_json,'$.creator_id')=coalesce(f.creator_id,''))
                    and not exists(select 1 from feedback b where b.event_type in ('dislike','dislike_video') and json_extract(b.extra_json,'$.file_id')=f.id)
                    and not exists(select 1 from feedback b where b.event_type='dislike_creator' and json_extract(b.extra_json,'$.creator_id')=coalesce(f.creator_id,''))
                    {source_sql}{creator_sql}
                    order by {order} limit ? offset ?'''
                params=[mode,mode]
                if creator_id: params.append(creator_id)
                params.extend([limit,page*limit]); self.send_json(rows(c,sql,params)); return
            if u.path=='/api/similar': self.send_json(rows(c,"""select s.a,s.b,s.reason,s.score,s.decision,s.note,
                fa.current_path path_a,coalesce(wa.title,'未命名') title_a,coalesce(ca.name,'博主待确认') creator_a,fa.status status_a,
                fb.current_path path_b,coalesce(wb.title,'未命名') title_b,coalesce(cb.name,'博主待确认') creator_b,fb.status status_b
                from similar s left join files fa on fa.id=s.a left join files fb on fb.id=s.b
                left join works wa on wa.id=fa.work_id left join works wb on wb.id=fb.work_id
                left join creators ca on ca.id=fa.creator_id left join creators cb on cb.id=fb.creator_id
                where coalesce(fa.status,'')!='已永久删除' and coalesce(fb.status,'')!='已永久删除' order by s.score desc limit 1000""")); return
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
                length=end-start+1; self.send_header('Accept-Ranges','bytes'); self.send_header('Content-Type','video/mp4'); self.send_header('Cache-Control','public, max-age=3600'); self.send_header('Content-Length',str(length)); self.end_headers()
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
        if u.path=='/api/history-import':
            script=Path(__file__).with_name('导入历史反馈.py'); py=os.environ.get('DOUYIN_PYTHON',sys.executable)
            try:
                p=subprocess.run([py,str(script)],cwd=str(BASE),capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=180)
                if p.returncode: self.send_json({'error':(p.stderr or p.stdout)[-4000:]},500); return
                self.send_json({'ok':True,'result':json.loads(p.stdout)}); return
            except Exception as e: self.send_json({'error':str(e)},500); return
        if u.path=='/api/follow':
            c=db()
            try:
                creator_id=str(data.get('creator_id') or '')
                if not c.execute('select 1 from creators where id=?',(creator_id,)).fetchone(): self.send_json({'error':'博主不存在'},404); return
                if data.get('follow'):
                    c.execute('insert or ignore into player_follows(creator_id,followed_at) values(?,?)',(creator_id,time.strftime('%Y-%m-%dT%H:%M:%S%z')))
                else:
                    c.execute('delete from player_follows where creator_id=?',(creator_id,))
                c.commit(); self.send_json({'ok':True,'following':bool(data.get('follow'))}); return
            finally: c.close()
        if not self.admin(): self.send_json({'error':'需要管理权限'},403); return
        if u.path=='/api/primary-mtime-plan':
            script=Path(__file__).with_name('恢复主库时间.py'); py=os.environ.get('DOUYIN_PYTHON',sys.executable)
            try:
                p=subprocess.run([py,str(script)],cwd=str(BASE),capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=180)
                if p.returncode:
                    self.send_json({'error':(p.stderr or p.stdout)[-4000:]},500); return
                self.send_json(json.loads(p.stdout)); return
            except Exception as exc:
                self.send_json({'error':str(exc)},500); return
        if u.path in ('/api/source-scan','/api/source-import'):
            import uuid
            job_id='sourcejob_'+uuid.uuid4().hex[:10]; script=Path(__file__).with_name('来源合并工具.py'); py=os.environ.get('DOUYIN_PYTHON',sys.executable)
            if u.path=='/api/source-scan':
                source=str(data.get('source') or '').strip()
                if not source or not Path(source).is_dir(): self.send_json({'error':'来源目录不存在'},400); return
                args=[py,str(script),'scan',source]
            else:
                batch=str(data.get('batch') or '')
                if not re.fullmatch(r'source_[A-Za-z0-9_]+',batch): self.send_json({'error':'扫描批次无效'},400); return
                args=[py,str(script),'import',batch]
                if data.get('accept_suspected'): args.append('--accept-suspected')
            JOBS[job_id]={'job_id':job_id,'status':'运行中'}
            def run_source():
                try:
                    p=subprocess.run(args,cwd=str(BASE),capture_output=True,text=True,encoding='utf-8',errors='replace')
                    JOBS[job_id].update(status='完成' if p.returncode==0 else '失败',output=(p.stdout or p.stderr)[-12000:])
                except Exception as e: JOBS[job_id].update(status='失败',output=str(e))
            threading.Thread(target=run_source,daemon=True).start(); self.send_json({'ok':True,'job_id':job_id}); return
        if u.path=='/api/primary-start':
            import uuid
            job_id='primaryjob_'+uuid.uuid4().hex[:10]; progress=LIB/'索引'/'主库优先整理'/'jobs'/f'{job_id}.json'; script=Path(__file__).with_name('主库优先整理.py'); py=os.environ.get('DOUYIN_PYTHON',sys.executable)
            JOBS[job_id]={'job_id':job_id,'status':'运行中','progress':str(progress)}
            def run_primary():
                try:
                    p=subprocess.run([py,str(script),'--progress',str(progress)],cwd=str(BASE),capture_output=True,text=True,encoding='utf-8',errors='replace')
                    JOBS[job_id].update(status='完成' if p.returncode==0 else '失败',output=(p.stdout or p.stderr)[-8000:])
                except Exception as e: JOBS[job_id].update(status='失败',output=str(e))
            threading.Thread(target=run_primary,daemon=True).start(); self.send_json({'ok':True,'job_id':job_id}); return
        if u.path=='/api/primary-index-start':
            active=next((x for x in JOBS.values() if x.get('kind')=='primary-index' and x.get('status')=='运行中'),None)
            if active: self.send_json({'ok':True,'job_id':active['job_id'],'message':'主库索引扫描已经在运行'}); return
            jobs_dir=LIB/'索引'/'主库优先整理'/'jobs'
            if jobs_dir.exists():
                for candidate in sorted(jobs_dir.glob('primaryindex_*.json'), key=lambda p:p.stat().st_mtime, reverse=True):
                    try: saved=json.loads(candidate.read_text(encoding='utf-8'))
                    except Exception: continue
                    saved=primary_runtime_state(saved)
                    if saved.get('status')=='运行中' and saved.get('worker_alive'):
                        job_id=candidate.stem
                        JOBS[job_id]={'job_id':job_id,'kind':'primary-index','status':'运行中','progress':str(candidate),'stop_file':str(candidate.with_suffix(candidate.suffix+'.stop'))}
                        self.send_json({'ok':True,'job_id':job_id,'message':'主库索引扫描已在运行，已接管任务'}); return
            import uuid
            job_id='primaryindex_'+uuid.uuid4().hex[:10]
            progress=LIB/'索引'/'主库优先整理'/'jobs'/f'{job_id}.json'
            stop_file=progress.with_suffix(progress.suffix+'.stop')
            stop_file.unlink(missing_ok=True)
            script=Path(__file__).with_name('扫描主库索引.py')
            py=os.environ.get('DOUYIN_PYTHON',sys.executable)
            JOBS[job_id]={'job_id':job_id,'kind':'primary-index','status':'运行中','progress':str(progress),'stop_file':str(stop_file)}
            def index_primary():
                try:
                    p=subprocess.Popen([py,str(script),'--progress',str(progress),'--stop-file',str(stop_file)],cwd=str(BASE),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace')
                    JOBS[job_id]['worker_pid']=p.pid
                    output,_=p.communicate()
                    JOBS[job_id].update(status='完成' if p.returncode==0 else '失败',output=(output or '')[-8000:],worker_pid=p.pid)
                except Exception as e:
                    JOBS[job_id].update(status='失败',output=str(e))
            threading.Thread(target=index_primary,daemon=True).start()
            self.send_json({'ok':True,'job_id':job_id}); return
        if u.path=='/api/primary-index-stop':
            job_id=str(data.get('job_id') or ''); job=JOBS.get(job_id)
            if not job:
                pp=primary_job_file(job_id)
                if pp:
                    try: saved=json.loads(pp.read_text(encoding='utf-8'))
                    except Exception: saved={}
                    saved=primary_runtime_state(saved)
                    if saved.get('status')=='运行中':
                        job={'job_id':job_id,'kind':'primary-index','status':'运行中','progress':str(pp),'stop_file':str(pp.with_suffix(pp.suffix+'.stop'))}
            if not job or job.get('kind')!='primary-index' or job.get('status')!='运行中':
                self.send_json({'error':'扫描任务当前没有运行'},409); return
            stop_file=Path(job['stop_file']); stop_file.parent.mkdir(parents=True,exist_ok=True); stop_file.write_text('stop',encoding='utf-8')
            self.send_json({'ok':True,'message':'已请求停止，扫描器会保存已完成部分'}); return
        if u.path=='/api/primary-apply':
            import uuid
            report=Path(str(data.get('report') or '')).resolve(); allowed=(LIB/'索引'/'主库优先整理').resolve()
            if not report.is_file() or not report.is_relative_to(allowed): self.send_json({'error':'整理报告无效'},400); return
            job_id='primaryapply_'+uuid.uuid4().hex[:10]; script=Path(__file__).with_name('应用主库整理报告.py'); py=os.environ.get('DOUYIN_PYTHON',sys.executable); JOBS[job_id]={'job_id':job_id,'status':'运行中'}
            def apply_primary():
                try:
                    p=subprocess.run([py,str(script),str(report)],cwd=str(BASE),capture_output=True,text=True,encoding='utf-8',errors='replace'); JOBS[job_id].update(status='完成' if p.returncode==0 else '失败',output=(p.stdout or p.stderr)[-8000:])
                except Exception as e: JOBS[job_id].update(status='失败',output=str(e))
            threading.Thread(target=apply_primary,daemon=True).start(); self.send_json({'ok':True,'job_id':job_id}); return
        if u.path=='/api/recycle-purge':
            import uuid
            job_id='recyclepurge_'+uuid.uuid4().hex[:10]; script=Path(__file__).with_name('清空虚拟回收站.py'); py=os.environ.get('DOUYIN_PYTHON',sys.executable); JOBS[job_id]={'job_id':job_id,'status':'运行中'}
            def purge():
                try:
                    p=subprocess.run([py,str(script),'--apply'],cwd=str(BASE),capture_output=True,text=True,encoding='utf-8',errors='replace'); JOBS[job_id].update(status='完成' if p.returncode==0 else '失败',output=(p.stdout or p.stderr)[-8000:])
                except Exception as e: JOBS[job_id].update(status='失败',output=str(e))
            threading.Thread(target=purge,daemon=True).start(); self.send_json({'ok':True,'job_id':job_id}); return
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
                c.execute('update creators set aliases_json=?,identity_status=? where id=?',(json.dumps(aliases,ensure_ascii=False),'人工确认（已合并身份）',primary))
                c.execute('update creators set identity_status=? where id=?',(f'已合并到:{primary}',secondary))
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









