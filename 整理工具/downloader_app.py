from __future__ import annotations
import csv, hashlib, json, os, queue, re, secrets, shutil, socket, subprocess, sys, threading, time, uuid, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from harvest_store import sync as sync_harvest_store

BASE=Path(__file__).resolve().parent.parent
LIB=BASE/'本地视频库'
ROOT=LIB/'下载器'; ROOT.mkdir(parents=True,exist_ok=True)
CREATORS=ROOT/'creators.json'; TASKS=LIB/'更新任务'; TASKS.mkdir(parents=True,exist_ok=True)
HOST=os.environ.get('DOUYIN_DOWNLOADER_HOST','127.0.0.1'); PORT=int(os.environ.get('DOUYIN_DOWNLOADER_PORT','8766')); JOBS={}; BROWSER=None; PLAYWRIGHT=None; BROWSER_THREAD=None; BROWSER_QUEUE=queue.Queue(); LOGIN_LOCK=threading.Lock()
LOGIN_BROWSER=None; LOGIN_CONTEXT=None; LOGIN_PLAYWRIGHT=None; LOGIN_PROCESS=None; LOGIN_THREAD=None; LOGIN_QUEUE=queue.Queue(); LOGIN_READY=threading.Event(); INTERACTIVE_LOCK=threading.Lock()
HARVEST_DIR=LIB/'关注归档'; HARVEST_DIR.mkdir(parents=True,exist_ok=True)
HARVEST_STATE=HARVEST_DIR/'采集进度.json'; HARVEST_LOCK=threading.Lock(); HARVEST_WRITE_LOCK=threading.RLock(); HARVEST_THREAD=None
HARVEST_DB=HARVEST_DIR/'采集索引.sqlite3'; HARVEST_DATA=HARVEST_DIR/'数据'
HARVEST_PAUSE=threading.Event(); HARVEST_STOP=threading.Event(); DOWNLOADER_SESSIONS={}
HARVEST_CLEAR_LOCK=threading.RLock(); HARVEST_CLEAR_THREAD=None; HARVEST_CLEAR_STATUS={'status':'空闲','message':'','backup':'','removed':0}
BACKLOG_THREAD=None; BACKLOG_LOCK=threading.RLock(); BACKLOG_STATUS={'status':'空闲','message':'','done':0,'total':0,'failed':0}
BATCHES={}; BATCH_LOCK=threading.RLock()
DOWNLOADER_CONFIG=ROOT/'web_config.json'

def _load_downloader_config():
    try: return json.loads(DOWNLOADER_CONFIG.read_text(encoding='utf-8'))
    except Exception:
        cfg={'management_password':os.environ.get('DOUYIN_DOWNLOADER_PASSWORD','123456'),'created_at':time.strftime('%Y-%m-%d %H:%M:%S')}
        DOWNLOADER_CONFIG.write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding='utf-8'); return cfg

DOWNLOADER_CFG=_load_downloader_config()

EDGE_EXE=Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
if not EDGE_EXE.exists(): EDGE_EXE=Path(r'C:\Program Files\Microsoft\Edge\Application\msedge.exe')
LOGIN_PROFILE=ROOT/'edge_login_profile'
LOGIN_CDP_PORT=int(os.environ.get('DOUYIN_LOGIN_CDP_PORT','9227'))

def read_creators():
    if not CREATORS.exists(): return []
    try: return json.loads(CREATORS.read_text(encoding='utf-8'))
    except Exception: return []
def write_creators(items): CREATORS.write_text(json.dumps(items,ensure_ascii=False,indent=2),encoding='utf-8')

def clear_creators():
    items=read_creators()
    stamp=time.strftime('%Y%m%d_%H%M%S')
    backup=CREATORS.with_name(f'creators_backup_{stamp}.json')
    if CREATORS.exists(): backup.write_text(json.dumps(items,ensure_ascii=False,indent=2),encoding='utf-8')
    write_creators([])
    return len(items),str(backup) if items else ''
def reply(h,obj,status=200):
    b=json.dumps(obj,ensure_ascii=False).encode(); h.send_response(status); h.send_header('Content-Type','application/json; charset=utf-8'); h.send_header('Content-Length',str(len(b))); h.end_headers(); h.wfile.write(b)

PAGE='''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>独立下载调试器</title>
<style>body{font:15px system-ui,"Microsoft YaHei";background:#f4f6fa;color:#202535;max-width:1200px;margin:auto;padding:18px}.panel{background:#fff;padding:18px;margin:12px 0;border-radius:12px;border:1px solid #e1e5ee}input,select,button{padding:9px;margin:4px;border:1px solid #bcc5d6;border-radius:7px;font:inherit}button{cursor:pointer;background:#315bdc;color:white;border:0}button.warn{background:#b7791f}button.dangerbtn{background:#b3261e}.muted{color:#687187}.item{padding:10px;border-top:1px solid #eee}.danger{color:#b3261e}.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px}.stat{background:#f7f8fc;border-radius:8px;padding:10px}.stat b{display:block;font-size:22px}.bar{height:10px;background:#e7ebf3;border-radius:8px;overflow:hidden}.bar i{display:block;height:100%;background:#315bdc}.scroll{max-height:480px;overflow:auto}.table{width:100%;border-collapse:collapse;font-size:13px}.table th,.table td{padding:7px;border-bottom:1px solid #e5e8ef;text-align:left;vertical-align:top}.table th{position:sticky;top:0;background:#f4f6fa}.hidden{display:none}</style>
<h1>独立下载控制台</h1><p class="muted">局域网监控与控制关注采集、主页解析、视频下载和 Excel 导出。只读查看无需密码，管理操作需要登录。</p>
<section class="panel"><h2>管理权限</h2><form id="adminForm" onsubmit="event.preventDefault();adminLogin()"><input id="adminPw" type="password" autocomplete="current-password" placeholder="管理密码"><button type="submit">进入管理模式</button><button type="button" onclick="adminLogout()">退出管理模式</button></form><span id="authmsg" class="muted">当前为只读模式</span></section>
<section class="panel"><h2>扫码登录与关系列表</h2><p class="muted">点击后会打开独立 Edge 窗口，在抖音页面扫码登录。账号密码不会写入本程序。</p><button onclick="login()">打开抖音扫码登录</button><button onclick="status()">检查登录状态</button><button onclick="follows()">读取我的关注</button><button onclick="fans()">读取我的粉丝</button><button onclick="selectAll()">全选当前列表</button><button onclick="saveSelected()">保存选中博主</button><button onclick="saveAndDownload()">保存选中并批量下载</button><p id="loginmsg" class="muted"></p><p id="batchmsg" class="muted"></p><div id="batchProgress"></div><div id="followList"></div></section>
<section class="panel"><h2>一次性采集全部关注博主</h2><p class="muted">读取全部关注主页中的可见视频和公开播放量、点赞量，逐博主保存进度；结束后生成总 Excel。视频下载到“关注归档”下的博主目录。访问受限或数据不可见时会保留状态并继续其他博主。</p><label>下载范围：<select id="downloadProfile"><option value="essence">精华视频（点赞靠前、近期和高播放）</option><option value="standard">标准视频（按博主数量和播放量自适应放宽）</option></select></label><span class="muted"> 标准档：作品少的博主基本全下，播放量整体偏低的博主降低门槛，并保留每位博主至少 10 条候选。</span><button onclick="startHarvest('incremental')">增量更新（新博主/新视频）</button><button onclick="startHarvest('standard')">标准更新（含近期统计）</button><button onclick="startHarvest('full')">完整复核</button><button onclick="startBacklog()">下载待下载清单（刷新 CDN）</button><button class="warn" onclick="controlHarvest('pause')">暂停</button><button onclick="controlHarvest('resume')">继续</button><button class="dangerbtn" onclick="controlHarvest('stop')">停止并保存断点</button><button onclick="controlHarvest('retry')">重试失败博主</button><button onclick="harvestStatus()">刷新进度</button><button onclick="openWorkbook()">打开 Excel</button><a href="/api/harvest-backlog-file">下载待下载 CSV</a><button class="dangerbtn" onclick="clearHarvest()">清空采集进度和内容</button><button class="warn" onclick="repairDuplicates()">扫描并移入错误归属视频</button><p id="harvestmsg" class="muted"></p><div id="harvest"></div><div class="stats" id="harvestStats"></div><h3>博主进度</h3><div class="scroll"><table class="table"><thead><tr><th>博主</th><th>状态/阶段</th><th>进度</th><th>作品</th><th>已下载</th><th>失败</th><th>主页</th></tr></thead><tbody id="creatorProgress"></tbody></table></div><h3>采集结果</h3><div class="toolbar"><input id="workSearch" placeholder="搜索博主、标题或作品ID" oninput="loadWorks()"><select id="workStatus" onchange="loadWorks()"><option value="">全部状态</option><option>已下载</option><option>已存在</option><option>无媒体地址</option><option>下载失败</option></select></div><div class="scroll"><table class="table"><thead><tr><th>博主</th><th>标题</th><th>播放量</th><th>点赞量</th><th>发布时间</th><th>下载状态</th><th>链接</th></tr></thead><tbody id="workRows"></tbody></table></div></section>
<section class="panel"><h2>导入博主</h2><input id="name" placeholder="博主名称"><input id="url" style="width:420px" placeholder="博主主页链接"><button onclick="add()">保存博主</button><button onclick="exportCreators()">导出全部博主 Excel</button><button class="dangerbtn" onclick="clearCreators()">清空所有博主</button><div id="list"></div></section>
<section class="panel"><h2>创建测试下载</h2><select id="creator"></select><input id="date" type="date"><select id="login"><option value="edge" selected>使用 Edge 登录状态</option><option value="">不使用登录</option><option value="chrome">使用 Chrome 登录状态</option><option value="firefox">使用 Firefox 登录状态</option><option value="file">使用 Cookie 文件</option></select><input id="cookie" style="width:360px" placeholder="Cookie 文件完整路径（仅选择文件时填写）"><button onclick="make()">创建并开始</button><p id="msg" class="muted"></p></section>
<section class="panel"><h2>任务状态</h2><div id="jobs">暂无任务</div></section>
<script>
let adminToken='';
const $=x=>document.querySelector(x), esc=x=>String(x??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
async function api(p,o={}){o.headers={'Content-Type':'application/json',...(o.headers||{}),...(adminToken?{'X-Admin-Token':adminToken}:{})};let r=await fetch(p,o);let x=await r.json();if(!r.ok)throw Error(x.error||'请求失败');return x}
async function adminLogin(){try{let r=await api('/api/admin-login',{method:'POST',body:JSON.stringify({password:$('#adminPw').value})});adminToken=r.token;$('#authmsg').textContent='管理模式已开启';$('#authmsg').className='';}catch(e){$('#authmsg').textContent=e.message;$('#authmsg').className='danger'}}
function adminLogout(){adminToken='';$('#authmsg').textContent='当前为只读模式';}
async function login(){try{ $('#loginmsg').textContent='正在打开登录窗口…'; let r=await api('/api/login',{method:'POST'});$('#loginmsg').textContent=r.message||r.error}catch(e){$('#loginmsg').textContent='打开登录窗口失败：'+e.message}}
async function status(){try{let r=await api('/api/login-status');$('#loginmsg').textContent=r.message||r.error}catch(e){$('#loginmsg').textContent='检查登录状态失败：'+e.message}}
async function startHarvest(mode='incremental'){let profile=$('#downloadProfile').value;let labels={incremental:'增量更新新博主和新视频',standard:'标准更新并复核近期统计',full:'完整复核所有可访问作品'};let names={essence:'精华视频',standard:'标准视频'};if(!confirm(`将执行${labels[mode]||mode}，下载范围：${names[profile]}。继续？`))return;try{let r=await api('/api/harvest',{method:'POST',body:JSON.stringify({download_profile:profile,essence_only:profile==='essence',mode})});$('#harvestmsg').textContent=r.message||r.error;harvestStatus()}catch(e){$('#harvestmsg').textContent=e.message}}
async function startBacklog(){if(!confirm('将读取待下载清单，并打开每个作品页刷新当天 CDN 地址后下载。继续？'))return;try{let r=await api('/api/harvest-backlog-download',{method:'POST',body:'{}'});$('#harvestmsg').textContent=r.message||r.error;pollBacklog()}catch(e){$('#harvestmsg').textContent='待下载任务启动失败：'+e.message}}
async function pollBacklog(){try{let r=await api('/api/harvest-backlog-status');$('#harvest').textContent=(r.message||'')+`；待下载 ${r.pending||0} 条，当前处理 ${r.done||0}/${r.total||0}，失败 ${r.failed||0}`;if(r.status==='运行中')setTimeout(pollBacklog,3000)}catch(e){}}
async function controlHarvest(action){try{let r=await api('/api/harvest-control',{method:'POST',body:JSON.stringify({action})});$('#harvestmsg').textContent=r.message||r.error;harvestStatus()}catch(e){$('#harvestmsg').textContent=e.message}}
function openWorkbook(){window.open('/api/harvest-file','_blank')}
async function clearHarvest(){if(!confirm('确定清空之前的采集进度和采集内容吗？系统会先完整备份“关注归档”，不会删除博主列表、待导入视频或下载任务。'))return;try{let r=await api('/api/harvest-clear',{method:'POST',body:'{}'});$('#harvestmsg').textContent=r.message||'已开始清空并备份';harvestStatus()}catch(e){$('#harvestmsg').textContent='清空失败：'+e.message}}
async function repairDuplicates(){if(!confirm('将按作品作者 ID 校验每个博主文件夹，并把错误归属文件移入虚拟回收站。不会永久删除。继续？'))return;try{let r=await api('/api/harvest-repair-duplicates',{method:'POST',body:'{}'});$('#harvestmsg').textContent=r.message||r.error;harvestStatus()}catch(e){$('#harvestmsg').textContent='归属修复失败：'+e.message}}
async function harvestStatus(){try{let r=await api('/api/harvest-status');$('#harvestmsg').textContent=r.message||r.error||'';$('#harvest').textContent=r.summary||'';let pct=r.creator_total?Math.round(100*r.creator_done/r.creator_total):0;$('#harvestStats').innerHTML=[['博主进度',r.creator_done+'/'+r.creator_total],['作品记录',r.work_count],['已验证作品',r.verified_work_count||0],['未验证链接',r.unverified_link_count||0],['已读取点赞量',r.digg_count],['已验证作品缺失点赞',r.verified_digg_missing||0],['有媒体链接',r.media_url_count||0],['已下载',r.downloaded],['错误',r.error_count]].map(x=>`<div class="stat">${esc(x[0])}<b>${esc(x[1])}</b></div>`).join('')+`<div class="stat">总进度<b>${pct}%</b><div class="bar"><i style="width:${pct}%"></i></div></div>`;$('#creatorProgress').innerHTML=(r.creator_records||[]).map(x=>{let p=Math.max(0,Math.min(100,Number(x.progress||0)));return `<tr><td>${esc(x.name)}</td><td>${esc(x.status||'')}${x.phase?`<br><span class=\"muted\">${esc(x.phase)}</span>`:''}</td><td><div class=\"bar\"><i style=\"width:${p}%\"></i></div><span class=\"muted\">${p}%</span></td><td>${x.works||0}</td><td>${x.downloaded||0}</td><td>${x.errors||0}</td><td><a href=\"${esc(x.url)}\" target=\"_blank\">打开</a></td></tr>`}).join('');loadWorks();if(r.running)setTimeout(harvestStatus,3000)}catch(e){$('#harvestmsg').textContent=e.message}}
async function loadWorks(){try{let q=new URLSearchParams({q:$('#workSearch').value||'',status:$('#workStatus').value||'',limit:'200'});let r=await api('/api/harvest-works?'+q.toString());$('#workRows').innerHTML=(r.items||[]).map(x=>`<tr><td>${esc(x.creator)}</td><td>${esc(x.title||x.aweme_id)}</td><td>${x.play_count??''}</td><td>${x.digg_count??''}</td><td>${esc(x.publish_time||'')}</td><td>${esc(x.download_status||'')}</td><td><a href="${esc(x.url)}" target="_blank">作品页</a></td></tr>`).join('')||'<tr><td colspan="7">暂无数据</td></tr>'}catch(e){}}
let relationItems=[];
function renderRelation(items){relationItems=items||[];$('#followList').innerHTML=relationItems.map((x,i)=>`<div class="item"><label><input class="relbox" type="checkbox" data-i="${i}"> <b>${esc(x.name)}</b></label><br><span class="muted">${esc(x.url)}</span></div>`).join('')||'<p class="muted">暂未读取到列表；请先扫码登录。</p>'}
async function follows(){let r=await api('/api/follows');$('#loginmsg').textContent=r.message||r.error;renderRelation(r.items)}
async function fans(){let r=await api('/api/fans');$('#loginmsg').textContent=r.message||r.error;renderRelation(r.items)}
function selected(){return [...document.querySelectorAll('.relbox:checked')].map(x=>relationItems[Number(x.dataset.i)]).filter(Boolean)}
function selectAll(){document.querySelectorAll('.relbox').forEach(x=>x.checked=true)}
async function saveSelected(){let items=selected();if(!items.length){alert('请先勾选博主');return}let r=await api('/api/creators/bulk',{method:'POST',body:JSON.stringify({items})});$('#loginmsg').textContent=r.message||r.error;load()}
async function saveAndDownload(){let items=selected();if(!items.length){alert('请先勾选博主');return}try{let r=await api('/api/bulk-download',{method:'POST',body:JSON.stringify({items,start_date:$('#date').value,cookie_mode:$('#login').value,cookie_file:$('#cookie').value})});$('#batchmsg').textContent=r.message||r.error;if(r.batch_id)pollBatch(r.batch_id);poll()}catch(e){$('#batchmsg').textContent='批量任务创建失败：'+e.message}}
async function pollBatch(id){try{let r=await api('/api/bulk-download-status?id='+encodeURIComponent(id));let rows=(r.items||[]).map(x=>`<div class="item"><b>${esc(x.name)}</b> · ${esc(x.status)}${x.task_id?' · '+esc(x.task_id):''}${x.error?' · <span class="danger">'+esc(x.error)+'</span>':''}</div>`).join('');$('#batchProgress').innerHTML=`<div>${esc(r.summary||'')}</div>${rows}`;if(r.status==='准备中'||r.status==='运行中')setTimeout(()=>pollBatch(id),2000)}catch(e){$('#batchmsg').textContent='批量任务状态读取失败：'+e.message}}
async function load(){let a=await api('/api/creators');$('#creator').innerHTML=a.map(x=>`<option value="${esc(x.id)}">${esc(x.name)} · ${esc(x.url)}</option>`).join('');$('#list').innerHTML=a.map(x=>`<div class="item"><b>${esc(x.name)}</b><br><span class="muted">${esc(x.url)}</span></div>`).join('')||'<p class="muted">还没有博主</p>';}
async function add(){let r=await api('/api/creators',{method:'POST',body:JSON.stringify({name:$('#name').value,url:$('#url').value})});$('#msg').textContent=r.message||r.error;$('#name').value='';$('#url').value='';load()}
async function exportCreators(){try{let r=await api('/api/creators-export',{method:'POST',body:'{}'});$('#msg').textContent=r.message||'已生成全部博主 Excel';window.open(r.url,'_blank')}catch(e){$('#msg').textContent='导出失败：'+e.message}}
async function clearCreators(){if(!confirm('确定清空已保存的全部博主吗？系统会先自动备份博主列表。已下载视频、任务文件和采集断点不会删除。'))return;try{let r=await api('/api/creators-clear',{method:'POST',body:'{}'});$('#msg').textContent=r.message||'已清空博主列表';load()}catch(e){$('#msg').textContent='清空失败：'+e.message}}
async function make(){let r=await api('/api/tasks',{method:'POST',body:JSON.stringify({creator_id:$('#creator').value,start_date:$('#date').value,cookie_mode:$('#login').value,cookie_file:$('#cookie').value})});$('#msg').textContent=r.message||r.error;if(r.task_id)run(r.task_id)}
async function run(id){let r=await api('/api/run',{method:'POST',body:JSON.stringify({task_id:id})});$('#msg').textContent=r.message||r.error;poll()}
async function poll(){let a=await api('/api/status');$('#jobs').innerHTML=a.map(x=>`<div class="item"><b>${esc(x.task_id)}</b> · ${esc(x.status)} · <span class="muted">日志：${esc(x.log||'')}</span></div>`).join('')||'暂无任务';if(a.some(x=>x.status==='运行中'))setTimeout(poll,1500)}
load();poll();harvestStatus();
</script>'''

def _browser_worker():
    global BROWSER, PLAYWRIGHT
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT=sync_playwright().start(); profile=ROOT/'edge_profile'; profile.mkdir(parents=True,exist_ok=True)
    def launch():
        nonlocal profile
        return PLAYWRIGHT.chromium.launch_persistent_context(str(profile),headless=False,executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',args=['--no-first-run','--no-default-browser-check','--new-window','--start-maximized'])
    BROWSER=launch()
    while True:
        fn, event, box=BROWSER_QUEUE.get()
        try:
            box['value']=fn(BROWSER)
        except Exception as e:
            # Edge may have been closed manually. Recreate the isolated context
            # and retry the requested action once.
            try:
                BROWSER=launch(); box['value']=fn(BROWSER)
            except Exception as e2: box['error']=str(e2)
        event.set()

def browser_call(fn, timeout=300):
    global BROWSER_THREAD
    with LOGIN_LOCK:
        if BROWSER_THREAD is None or not BROWSER_THREAD.is_alive():
            BROWSER_THREAD=threading.Thread(target=_browser_worker,daemon=True); BROWSER_THREAD.start()
            for _ in range(100):
                if BROWSER is not None: break
                time.sleep(.05)
        event=threading.Event(); box={}; BROWSER_QUEUE.put((fn,event,box))
        if not event.wait(timeout): raise TimeoutError(f'页面读取超过 {timeout} 秒，请保持登录窗口打开后重试')
        if 'error' in box: raise RuntimeError(box['error'])
        return box.get('value')

def _interactive_browser_worker():
    """Own a separate visible Edge context for short interactive actions.

    The harvest worker can spend minutes on one creator. Keeping login/status
    actions on this independent context prevents the UI buttons from waiting
    behind a long harvest request.
    """
    global LOGIN_BROWSER, LOGIN_CONTEXT, LOGIN_PLAYWRIGHT, LOGIN_PROCESS
    from playwright.sync_api import sync_playwright
    LOGIN_PROFILE.mkdir(parents=True,exist_ok=True)
    def launch_context():
        global LOGIN_BROWSER, LOGIN_CONTEXT, LOGIN_PLAYWRIGHT, LOGIN_PROCESS
        if LOGIN_PLAYWRIGHT is None: LOGIN_PLAYWRIGHT=sync_playwright().start()
        # Reuse an already-running isolated Edge when its CDP endpoint is
        # available. This prevents Edge's single-instance behavior from
        # swallowing a new launch and leaving the button waiting forever.
        try:
            with socket.create_connection(('127.0.0.1',LOGIN_CDP_PORT),timeout=.5): pass
            LOGIN_BROWSER=LOGIN_PLAYWRIGHT.chromium.connect_over_cdp(f'http://127.0.0.1:{LOGIN_CDP_PORT}')
            LOGIN_CONTEXT=LOGIN_BROWSER.contexts[0] if LOGIN_BROWSER.contexts else LOGIN_BROWSER.new_context()
            page=LOGIN_CONTEXT.pages[0] if LOGIN_CONTEXT.pages else LOGIN_CONTEXT.new_page()
            try: page.bring_to_front()
            except Exception: pass
            LOGIN_READY.set(); return
        except Exception: pass
        if LOGIN_BROWSER is not None:
            try: LOGIN_BROWSER.close()
            except Exception: pass
        # A previous run can leave the profile's first Edge process alive
        # without the CDP port (Edge reuses that process and silently ignores
        # the new --remote-debugging-port argument).  Recycle only processes
        # using this isolated login profile; never touch the user's normal Edge.
        try:
            import psutil
            profile_text=str(LOGIN_PROFILE).lower()
            for proc in psutil.process_iter(['pid','cmdline']):
                try:
                    cmd=' '.join(proc.info.get('cmdline') or []).lower()
                    if 'msedge' in cmd and profile_text in cmd and proc.pid != os.getpid():
                        proc.terminate()
                except Exception: pass
            time.sleep(.5)
        except Exception: pass
        if LOGIN_PROCESS is not None:
            try:
                if LOGIN_PROCESS.poll() is None: LOGIN_PROCESS.terminate()
            except Exception: pass
        # Launch Edge as a normal Windows desktop process. Playwright's
        # headful pipe can create a browser process without a visible window
        # when the service is running in a background console; CDP keeps the
        # window owned by the desktop while still allowing automation.
        LOGIN_PROFILE.mkdir(parents=True,exist_ok=True)
        flags=getattr(subprocess,'CREATE_NEW_CONSOLE',0x00000010)
        LOGIN_PROCESS=subprocess.Popen([str(EDGE_EXE),f'--user-data-dir={LOGIN_PROFILE}',f'--remote-debugging-port={LOGIN_CDP_PORT}','--new-window','https://www.douyin.com/'],creationflags=flags,close_fds=True)
        deadline=time.time()+20
        while time.time()<deadline:
            try:
                with socket.create_connection(('127.0.0.1',LOGIN_CDP_PORT),timeout=.4): break
            except OSError: time.sleep(.25)
        else: raise RuntimeError('Edge 调试端口未启动，请检查窗口是否被系统拦截')
        LOGIN_BROWSER=LOGIN_PLAYWRIGHT.chromium.connect_over_cdp(f'http://127.0.0.1:{LOGIN_CDP_PORT}')
        LOGIN_CONTEXT=LOGIN_BROWSER.contexts[0] if LOGIN_BROWSER.contexts else LOGIN_BROWSER.new_context()
        page=LOGIN_CONTEXT.pages[0] if LOGIN_CONTEXT.pages else LOGIN_CONTEXT.new_page()
        try: page.goto('https://www.douyin.com/',wait_until='domcontentloaded',timeout=30000); page.bring_to_front()
        except Exception: pass
        LOGIN_READY.set()
    try:
        launch_context()
        while True:
            fn,event,box=LOGIN_QUEUE.get()
            try:
                if LOGIN_BROWSER is None or LOGIN_BROWSER.is_connected() is False or LOGIN_CONTEXT is None: launch_context()
                box['value']=fn(LOGIN_CONTEXT)
            except Exception as exc:
                # Edge can be closed manually. Recreate the isolated context
                # once and retry the short interactive operation.
                try:
                    launch_context(); box['value']=fn(LOGIN_CONTEXT)
                except Exception as exc2: box['error']=str(exc2)
            event.set()
    except Exception as exc:
        LOGIN_READY.set()
        while True:
            fn,event,box=LOGIN_QUEUE.get(); box['error']=str(exc); event.set()

def ensure_interactive_browser():
    global LOGIN_THREAD
    if LOGIN_THREAD is None or not LOGIN_THREAD.is_alive():
        LOGIN_READY.clear(); LOGIN_THREAD=threading.Thread(target=_interactive_browser_worker,daemon=True); LOGIN_THREAD.start()

def interactive_browser_call(fn, timeout=30):
    ensure_interactive_browser()
    if not LOGIN_READY.wait(timeout): raise TimeoutError('登录浏览器启动超时，请检查 Edge 是否可用')
    with INTERACTIVE_LOCK:
        event=threading.Event(); box={}; LOGIN_QUEUE.put((fn,event,box))
        if not event.wait(timeout): raise TimeoutError('登录浏览器操作超时，请保持登录窗口打开')
        if 'error' in box: raise RuntimeError(box['error'])
        return box.get('value')

def open_login():
    try:
        # Start the independent interactive worker asynchronously. Its visible
        # persistent context opens the QR login page without waiting for the
        # long-running harvest context.
        ensure_interactive_browser()
        return {'ok':True,'message':'已打开独立抖音登录窗口，请用手机扫码；完成后点击“检查登录状态”。'}
    except Exception as e: return {'error':f'无法打开登录窗口：{e}'}

def login_status():
    try:
        def work(ctx):
            page=ctx.pages[0] if ctx.pages else ctx.new_page(); text=page.locator('body').inner_text(timeout=5000); return not any(x in text for x in ('登录','扫码登录')) and len(ctx.cookies())>0
        logged=interactive_browser_call(work,timeout=15); return {'ok':True,'logged_in':logged,'message':'已检测到登录状态。' if logged else '尚未确认登录，请在 Edge 窗口完成扫码。'}
    except Exception as e: return {'error':f'检测失败：{e}'}

def sync_login_to_harvest():
    """Copy cookies from the interactive QR session into the harvest context."""
    cookies=interactive_browser_call(lambda ctx: ctx.cookies(),timeout=20)
    if not cookies: raise RuntimeError('登录浏览器中没有可用 Cookie，请先完成扫码登录')
    browser_call(lambda ctx: ctx.add_cookies(cookies),timeout=20)
    return len(cookies)

def read_follows():
    return read_relation('follow')

def read_fans():
    return read_relation('fans')

def read_relation(kind):
    try:
        def work(ctx):
            page=ctx.pages[0] if ctx.pages else ctx.new_page(); page.goto('https://www.douyin.com/user/self',wait_until='domcontentloaded',timeout=30000); page.bring_to_front(); page.wait_for_timeout(6000)
            # The tab query has changed several times. Prefer the visible
            # “关注” tab when the profile page renders it.
            labels=('关注','我的关注','Following') if kind=='follow' else ('粉丝','我的粉丝','Followers')
            for label in labels:
                try:
                    loc=page.get_by_text(label,exact=True).last
                    if loc.is_visible(timeout=800): loc.click(); page.wait_for_timeout(2500); break
                except Exception: pass
            items=[]; seen=set(); unchanged=0; reported_total=None; reached_end=False
            for _ in range(140):
                batch=page.evaluate("""() => Array.from(document.querySelectorAll('a[href*="/user/"]')).map(a => {
                  let e=a, followed=false;
                  for(let i=0;i<7 && e;i++,e=e.parentElement) if((e.innerText||'').includes('已关注')) followed=true;
                  return {name:(a.innerText||'').trim(), href:a.href||a.getAttribute('href')||'', followed};
                }).filter(x => x.followed)""")
                before=len(items)
                for row in batch:
                    name=str(row.get('name') or '').strip(); full=str(row.get('href') or '').strip()
                    if not name or not full or name in ('我的','关注','朋友','粉丝'): continue
                    full=full.replace('https://www.douyin.com//www.douyin.com/','https://www.douyin.com/')
                    if re.fullmatch(r'https?://(?:www\.)?douyin\.com/user/self(?:\?.*)?',full): continue
                    if full not in seen:
                        seen.add(full); items.append({'name':name.lstrip('@'),'url':full})
                unchanged=unchanged+1 if len(items)==before else 0
                body_text=page.locator('body').inner_text(timeout=3000)
                m=re.search(r'(?:关注|粉丝|Following|Followers)\s*[\\r\\n ]+\(?([0-9][0-9+]*)',body_text)
                if m:
                    raw=m.group(1).replace('+','')
                    try: reported_total=int(raw)
                    except Exception: pass
                reached_end=('暂时没有更多了' in body_text or '没有更多' in body_text or 'No more' in body_text)
                if reached_end and (reported_total is None or len(items)>=reported_total): break
                if unchanged>=12: break
                # Virtualized lists recycle DOM nodes. Scroll every likely list
                # container and the page, then collect the current batch again.
                page.evaluate("""() => {
                  const all=[document.scrollingElement,...document.querySelectorAll('*')];
                  const boxes=all.filter(e=>e && e.scrollHeight>e.clientHeight+80 && getComputedStyle(e).overflowY!=='hidden');
                  boxes.sort((a,b)=>(b.scrollHeight-b.clientHeight)-(a.scrollHeight-a.clientHeight));
                  for(const e of boxes.slice(0,4)) e.scrollTop=Math.min(e.scrollTop+Math.max(700,e.clientHeight*.85),e.scrollHeight);
                  window.scrollBy(0,1000);
                }""")
                page.wait_for_timeout(1000)
            return {'items':items,'reported_total':reported_total,'complete':bool(reached_end and (reported_total is None or len(items)>=reported_total))}
        result=interactive_browser_call(work, timeout=300) or {'items':[]}; items=result.get('items') or []; reported_total=result.get('reported_total'); complete=bool(result.get('complete'))
        kind_name='关注博主' if kind=='follow' else '粉丝主页'
        suffix=f'（页面显示总数 {reported_total}）' if reported_total is not None else ''
        message=f'读取到 {len(items)} 个{kind_name}{suffix}。'+('已到列表末尾。' if complete else '列表可能仍有未加载内容，请继续滚动或稍后重试。')
        return {'ok':True,'items':items,'reported_total':reported_total,'complete':complete,'message':message}
    except Exception as e: return {'error':f'读取关注列表失败：{e}'}

def page_debug():
    try:
        def work(ctx):
            page=ctx.pages[0] if ctx.pages else ctx.new_page(); return {'url':page.url,'title':page.title(),'text':page.locator('body').inner_text(timeout=5000)[:2000]}
        return {'ok':True,**interactive_browser_call(work)}
    except Exception as e: return {'error':str(e)}

def _walk_awemes(obj):
    """Yield Douyin aweme objects from common web API response shapes."""
    if isinstance(obj,dict):
        for key in ('aweme_list','awemeList'):
            value=obj.get(key)
            if isinstance(value,list):
                for item in value:
                    if isinstance(item,dict) and (item.get('aweme_id') or item.get('awemeId')): yield item
        for key,value in obj.items():
            if key not in ('aweme_list','awemeList'):
                yield from _walk_awemes(value)
    elif isinstance(obj,list):
        for value in obj: yield from _walk_awemes(value)

def _metric(item, key):
    stats=item.get('statistics') or item.get('stats') or {}
    value=stats.get(key)
    try: return int(value) if value is not None and str(value).isdigit() else None
    except Exception: return None

def _aweme_record(item, creator):
    aid=str(item.get('aweme_id') or item.get('awemeId') or '')
    desc=str(item.get('desc') or item.get('title') or '').strip()
    author=item.get('author') or {}
    video=item.get('video') or {}
    urls=[]
    for obj in (video.get('play_addr') or {},video.get('download_addr') or {}):
        for url in obj.get('url_list') or []:
            if url and url not in urls: urls.append(url)
    created=item.get('create_time') or item.get('createTime')
    try: created=time.strftime('%Y-%m-%d %H:%M:%S',time.localtime(int(created))) if created else ''
    except Exception: created=''
    return {'creator':creator.get('name',''),'creator_url':creator.get('url',''),'creator_sec_uid':author.get('sec_uid') or author.get('secUid') or '',
      'aweme_id':aid,'title':desc,'publish_time':created,'url':f'https://www.douyin.com/video/{aid}' if aid else '',
      'play_count':_metric(item,'play_count'),'digg_count':_metric(item,'digg_count'),'collect_count':_metric(item,'collect_count'),
      'comment_count':_metric(item,'comment_count'),'share_count':_metric(item,'share_count'),'duration_ms':(video.get('duration') if isinstance(video,dict) else None),
      'media_urls':urls,'verified_aweme':True,'read_status':'已读取统计' if _metric(item,'digg_count') is not None or _metric(item,'play_count') is not None else '统计不可用'}

def _creator_id_from_url(url):
    """Return the stable user id from a Douyin /user/<id> URL."""
    try:
        path=urlparse(str(url or '')).path.rstrip('/')
        if '/user/' in path: return path.rsplit('/user/',1)[1].split('/',1)[0]
    except Exception: pass
    return ''

def _aweme_belongs_to_creator(item, creator):
    """Reject recommendation-card works returned beside a creator's works."""
    expected=_creator_id_from_url(creator.get('url',''))
    if not expected: return True
    author=item.get('author') or {}
    actual=str(author.get('sec_uid') or author.get('secUid') or '').strip()
    # Some lightweight responses omit author data. Keep those for now; the
    # visible-link fallback still preserves the work URL for later checking.
    return not actual or actual==expected

def _load_harvest_state():
    try: return json.loads(HARVEST_STATE.read_text(encoding='utf-8'))
    except Exception: return {'status':'空闲','creators':[],'works':[],'errors':[],'updated_at':''}

def _save_harvest_state(state):
    with HARVEST_WRITE_LOCK:
        state['updated_at']=time.strftime('%Y-%m-%d %H:%M:%S')
        # SQLite and the readable JSON mirrors are updated before the legacy
        # aggregate state file. A failure here is recorded but never hides the
        # harvest checkpoint itself.
        try:
            db_snapshot,snapshot=sync_harvest_store(state,HARVEST_DB,HARVEST_DATA)
            state['dual_store']={'sqlite':str(HARVEST_DB),'json_dir':str(HARVEST_DATA),'last_sqlite_snapshot':db_snapshot,'last_json_snapshot':snapshot,'synced_at':state['updated_at']}
        except Exception as exc:
            state.setdefault('errors',[]).append({'creator':'双存储同步','error':str(exc)})
        tmp=HARVEST_STATE.with_suffix('.tmp'); tmp.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8'); tmp.replace(HARVEST_STATE)
        _write_download_backlog(state)

def _write_download_backlog(state):
    """Persist retryable works with stable Douyin URLs, never only CDN URLs."""
    rows=[]
    for w in state.get('works',[]):
        status=str(w.get('download_status') or '未尝试')
        if status in ('已下载','已存在','未验证链接（仅保留链接）'):
            continue
        rows.append({
            'creator':w.get('creator',''), 'creator_url':w.get('creator_url',''),
            'aweme_id':w.get('aweme_id',''), 'title':w.get('title',''),
            'publish_time':w.get('publish_time',''), 'play_count':w.get('play_count'),
            'digg_count':w.get('digg_count'), 'collect_count':w.get('collect_count'),
            'url':w.get('url',''), 'download_status':status,
            'selection_reason':w.get('selection_reason',''),
            'last_cdn_seen_at':w.get('last_cdn_seen_at',''),
            'last_error':w.get('download_error','')
        })
    out=HARVEST_DIR/'待下载清单.json'; tmp=out.with_suffix('.tmp')
    tmp.write_text(json.dumps({'generated_at':time.strftime('%Y-%m-%d %H:%M:%S'),'items':rows},ensure_ascii=False,indent=2),encoding='utf-8'); tmp.replace(out)
    csv_path=HARVEST_DIR/'待下载清单.csv'
    with csv_path.open('w',encoding='utf-8-sig',newline='') as f:
        fields=['creator','creator_url','aweme_id','title','publish_time','play_count','digg_count','collect_count','url','download_status','selection_reason','last_cdn_seen_at','last_error']
        writer=csv.DictWriter(f,fieldnames=fields); writer.writeheader(); writer.writerows(rows)

def harvest_status():
    state=_load_harvest_state(); status=state.get('status','空闲'); works=state.get('works',[])
    alive=HARVEST_THREAD is not None and HARVEST_THREAD.is_alive()
    if status=='运行中' and not alive:
        status='暂停'; state['status']='暂停'; state['phase']='已暂停（服务重启或线程已结束）'; _save_harvest_state(state)
    running=status=='运行中' and alive and not HARVEST_PAUSE.is_set()
    verified=[w for w in works if w.get('verified_aweme',w.get('read_status')!='仅发现链接，统计待补')]
    return {'status':status,'running':running,'thread_alive':alive,'mode':state.get('harvest_mode','incremental'),'download_profile':state.get('download_profile') or ('essence' if state.get('essence_only') else 'all'),'phase':state.get('phase','-'),'creator_done':state.get('creator_done',0),'creator_total':state.get('creator_total',0),'work_count':len(works),'verified_work_count':len(verified),'unverified_link_count':len(works)-len(verified),'play_count':sum(1 for w in works if w.get('play_count') is not None),'digg_count':sum(1 for w in works if w.get('digg_count') is not None),'play_missing':sum(1 for w in works if w.get('play_count') is None),'digg_missing':sum(1 for w in works if w.get('digg_count') is None),'verified_digg_missing':sum(1 for w in verified if w.get('digg_count') is None),'media_url_count':sum(1 for w in works if w.get('media_urls')),'downloaded':sum(1 for w in works if w.get('download_status') in ('已下载','已存在')),'extinct_count':sum(1 for w in works if str(w.get('availability_status','')).startswith('绝版')),'error_count':len(state.get('errors',[])),'creator_records':state.get('creator_records',[]),'dual_store':state.get('dual_store',{}),'summary':f"阶段：{state.get('phase','-')}；博主 {state.get('creator_done',0)}/{state.get('creator_total',0)}；范围 {state.get('download_profile') or ('精华' if state.get('essence_only') else '全部')}；作品 {len(works)}；失败记录 {len(state.get('errors',[]))}；最近更新 {state.get('updated_at','-')}",
      'message':f"采集任务{status}。" if status!='运行中' else '采集中，保持登录窗口开启。'}

def harvest_works(query):
    state=_load_harvest_state(); q=str(query.get('q',[''])[0]).strip().lower(); status=str(query.get('status',[''])[0]).strip(); limit=min(max(int(query.get('limit',['200'])[0] or 200),1),1000); items=[]
    for w in reversed(state.get('works',[])):
        hay=' '.join(str(w.get(k) or '') for k in ('creator','title','aweme_id','url')).lower()
        if q and q not in hay: continue
        if status and str(w.get('download_status') or '')!=status: continue
        items.append(w)
        if len(items)>=limit: break
    return {'items':items,'total':len(state.get('works',[]))}

def _set_creator_record(state, idx, **updates):
    records=state.setdefault('creator_records',[])
    while len(records)<=idx: records.append({})
    records[idx].update(updates)

class HarvestStopped(Exception): pass

def _harvest_checkpoint(state):
    if HARVEST_STOP.is_set(): raise HarvestStopped()
    while HARVEST_PAUSE.is_set() and not HARVEST_STOP.is_set():
        if state.get('status')!='暂停': state['status']='暂停'; state['phase']='已暂停'; _save_harvest_state(state)
        time.sleep(.5)
    if HARVEST_STOP.is_set(): raise HarvestStopped()
    if state.get('status')=='暂停': state['status']='运行中'; _save_harvest_state(state)

def _collect_creator_all(ctx, creator, progress=None):
    page=ctx.pages[0] if ctx.pages else ctx.new_page(); found={}; more={'value':True}; last_error=[]
    def on_response(response):
        u=response.url.lower()
        if not any(x in u for x in ('aweme','post','work','item_list')): return
        try:
            payload=response.json()
            for aweme in _walk_awemes(payload):
                if not _aweme_belongs_to_creator(aweme,creator): continue
                rec=_aweme_record(aweme,creator)
                if rec['aweme_id']: found[rec['aweme_id']]=rec
            # Common Douyin response shapes report continuation at top level or in data.
            def find_more(x):
                if isinstance(x,dict):
                    for k in ('has_more','hasMore'):
                        if k in x: more['value']=bool(x[k])
                    for v in x.values(): find_more(v)
                elif isinstance(x,list):
                    for v in x: find_more(v)
            find_more(payload)
        except Exception as exc: last_error.append(str(exc))
    page.on('response',on_response)
    try:
        if progress: progress(5,'打开博主主页')
        page.goto(creator['url'],wait_until='domcontentloaded',timeout=45000); page.bring_to_front(); page.wait_for_timeout(5000)
        for label in ('作品','视频','Posts'):
            try:
                loc=page.get_by_text(label,exact=True).last
                if loc.is_visible(timeout=700):
                    if progress: progress(10,'打开作品列表')
                    loc.click(); page.wait_for_timeout(2500); break
            except Exception: pass
        unchanged=0; prior=-1; bottom_stalls=0
        for _ in range(5000):
            loop_no=_
            if progress and (loop_no % 4 == 0): progress(min(90,12+loop_no//4),'滚动加载作品')
            # Also collect visible links; API response capture remains authoritative for metrics.
            links=page.evaluate("""() => Array.from(document.querySelectorAll('a[href*="/video/"]')).map(a=>a.href||'')""") or []
            for href in links:
                m=re.search(r'/video/(\d+)',str(href))
                if m and m.group(1) not in found:
                    aid=m.group(1); found[aid]={'creator':creator['name'],'creator_url':creator['url'],'creator_sec_uid':'','aweme_id':aid,'title':'','publish_time':'','url':'https://www.douyin.com/video/'+aid,'play_count':None,'digg_count':None,'collect_count':None,'comment_count':None,'share_count':None,'duration_ms':None,'media_urls':[],'verified_aweme':False,'read_status':'仅发现链接，统计待补'}
            if len(found)==prior: unchanged+=1
            else: unchanged=0; prior=len(found)
            if more['value'] is False and unchanged>=5: break
            if unchanged>=24: break
            before=page.evaluate('() => document.scrollingElement?.scrollTop || 0')
            page.evaluate("""() => { const all=[document.scrollingElement,...document.querySelectorAll('*')]; const boxes=all.filter(e=>e&&e.scrollHeight>e.clientHeight+100&&getComputedStyle(e).overflowY!=='hidden').sort((a,b)=>(b.scrollHeight-b.clientHeight)-(a.scrollHeight-a.clientHeight)); for(const e of boxes.slice(0,4))e.scrollTop=Math.min(e.scrollTop+Math.max(700,e.clientHeight*.8),e.scrollHeight); window.scrollBy(0,1200); }""")
            page.wait_for_timeout(900)
            after=page.evaluate('() => document.scrollingElement?.scrollTop || 0')
            bottom_stalls=bottom_stalls+1 if after==before else 0
            if bottom_stalls>=18 and unchanged>=12: break
        if progress: progress(100,'采集完成，整理作品数据')
        return list(found.values()),('主页未公开完整作品数据' if last_error and not found else '')
    finally:
        try: page.remove_listener('response',on_response)
        except Exception: pass

def _download_aweme(rec, folder, ctx):
    aid=rec.get('aweme_id') or 'unknown'; target=folder/(aid+'.mp4')
    if target.exists() and target.stat().st_size>0: return '已存在',str(target)
    urls=rec.get('media_urls') or []
    if not urls: return '无媒体地址', ''
    errors=[]
    for url in urls[:3]:
        for attempt in range(3):
            try:
                response=ctx.request.get(url,timeout=60000,headers={'Referer':'https://www.douyin.com/','User-Agent':'Mozilla/5.0'})
                if not response.ok: raise RuntimeError(f'HTTP {response.status}')
                data=response.body()
                if len(data)<1024: raise RuntimeError(f'响应过小：{len(data)} bytes')
                tmp=target.with_suffix('.mp4.part'); tmp.write_bytes(data); tmp.replace(target)
                return '已下载',str(target)
            except Exception as exc:
                errors.append(str(exc)); time.sleep(min(2**attempt,5))
    return '下载失败',errors[-1] if errors else '媒体地址失效'

def _download_aweme_http(rec, folder):
    """Thread-safe downloader for already refreshed CDN URLs."""
    aid=rec.get('aweme_id') or 'unknown'; target=folder/(aid+'.mp4')
    if target.exists() and target.stat().st_size>0: return '已存在',str(target)
    urls=rec.get('media_urls') or []
    if not urls: return '无媒体地址',''
    errors=[]
    for url in urls[:3]:
        for attempt in range(2):
            try:
                req=urllib.request.Request(url,headers={'Referer':'https://www.douyin.com/','User-Agent':'Mozilla/5.0'})
                with urllib.request.urlopen(req,timeout=60) as response:
                    if getattr(response,'status',200) in (403,429): raise RuntimeError(f'HTTP {response.status}')
                    data=response.read()
                if len(data)<1024: raise RuntimeError(f'响应过小：{len(data)} bytes')
                tmp=target.with_suffix('.mp4.part'); tmp.write_bytes(data); tmp.replace(target)
                return '已下载',str(target)
            except Exception as exc:
                errors.append(str(exc)); time.sleep(min(1.5*(attempt+1),3))
    return '下载失败',errors[-1] if errors else '媒体地址失效'

def _refresh_aweme_media(rec, ctx):
    """Refresh short-lived CDN URLs from the stable Douyin work page."""
    url=rec.get('url') or ''
    if not url: return False
    page=ctx.pages[0] if ctx.pages else ctx.new_page(); found={}
    def on_response(response):
        try:
            if not any(x in response.url.lower() for x in ('aweme','post','work','item_list')): return
            payload=response.json()
            for item in _walk_awemes(payload):
                aid=str(item.get('aweme_id') or item.get('awemeId') or '')
                if aid==str(rec.get('aweme_id')): found['item']=item
        except Exception: pass
    page.on('response',on_response)
    try:
        page.goto(url,wait_until='domcontentloaded',timeout=45000); page.bring_to_front(); page.wait_for_timeout(3500)
        rec['last_checked_at']=time.strftime('%Y-%m-%d %H:%M:%S')
        item=found.get('item')
        if item:
            fresh=_aweme_record(item,{'name':rec.get('creator',''),'url':rec.get('creator_url','')})
            for key in ('title','publish_time','play_count','digg_count','collect_count','comment_count','share_count','duration_ms','media_urls','read_status'):
                if fresh.get(key) not in (None,'',[]): rec[key]=fresh[key]
            rec['last_cdn_seen_at']=time.strftime('%Y-%m-%d %H:%M:%S')
            rec['availability_status']='正常'; rec['availability_failures']=0
            return bool(rec.get('media_urls'))
        try:
            body=page.locator('body').inner_text(timeout=3000)
        except Exception: body=''
        rec['availability_failures']=int(rec.get('availability_failures') or 0)+1
        if any(x in body for x in ('作品不存在','作品已删除','内容不可见','视频不存在')):
            rec['availability_status']='绝版-作品已删除'
        elif rec['availability_failures']>=2:
            rec['availability_status']='绝版-待人工确认'
        else:
            rec['availability_status']='暂时不可访问'
        return False
    finally:
        try: page.remove_listener('response',on_response)
        except Exception: pass

def _download_backlog_worker():
    global BACKLOG_STATUS
    state=_load_harvest_state(); items=[w for w in state.get('works',[]) if str(w.get('download_status') or '') in ('','未选择（精华筛选）','下载失败','无媒体地址','未尝试','刷新失败')]
    BACKLOG_STATUS={'status':'运行中','message':'正在刷新作品页并下载待下载清单','done':0,'total':len(items),'failed':0}
    if not items:
        BACKLOG_STATUS={'status':'完成','message':'待下载清单为空','done':0,'total':0,'failed':0}; return
    try:
        def work(ctx):
            for rec in items:
                creator=rec.get('creator') or '未知博主'; group=rec.get('creator_url') or creator
                folder=HARVEST_DIR/(re.sub(r'[\\/:*?"<>|]','_',creator)+'__'+hashlib.sha1(str(group).encode()).hexdigest()[:8]); folder.mkdir(parents=True,exist_ok=True)
                ok=_refresh_aweme_media(rec,ctx)
                if ok:
                    status,path=_download_aweme(rec,folder,ctx); rec['download_status']=status; rec['local_path']=path if status in ('已下载','已存在') else ''
                    if status=='下载失败': rec['download_error']=path
                else:
                    rec['download_status']='刷新失败'; rec['download_error']='作品页未返回新的媒体地址'
                BACKLOG_STATUS['done']+=1
                if rec.get('download_status') not in ('已下载','已存在'): BACKLOG_STATUS['failed']+=1
                state['works']=list({str(w.get('aweme_id')):w for w in state.get('works',[])}.values()); _save_harvest_state(state)
        interactive_browser_call(work,timeout=86400)
        BACKLOG_STATUS['status']='完成'; BACKLOG_STATUS['message']=f"待下载清单处理完成，失败 {BACKLOG_STATUS['failed']} 条"
    except Exception as exc:
        BACKLOG_STATUS['status']='失败'; BACKLOG_STATUS['message']=str(exc)

def start_backlog_download():
    global BACKLOG_THREAD
    with BACKLOG_LOCK:
        if HARVEST_THREAD is not None and HARVEST_THREAD.is_alive():
            return {'ok':False,'message':'当前采集任务仍在运行，请先暂停或完成采集后再下载待下载清单。'}
        if BACKLOG_THREAD is not None and BACKLOG_THREAD.is_alive(): return {'ok':True,'message':'待下载任务已经在运行。'}
        BACKLOG_THREAD=threading.Thread(target=_download_backlog_worker,daemon=True); BACKLOG_THREAD.start()
    return {'ok':True,'message':'已开始刷新 CDN 地址并下载待下载清单。'}

def backlog_status():
    state=_load_harvest_state()
    pending=sum(1 for w in state.get('works',[]) if str(w.get('download_status') or '') in ('','未选择（精华筛选）','下载失败','无媒体地址','未尝试','刷新失败'))
    result=dict(BACKLOG_STATUS); result['pending']=pending
    return result

def _essence_aweme_ids(works):
    """Return IDs selected by the single 精华 download rule."""
    n=len(works)
    if n <= 20: quota=n
    elif n <= 50: quota=max(10, (n*50 + 99)//100)
    elif n <= 100: quota=max(15, (n*40 + 99)//100)
    elif n <= 300: quota=max(20, (n*30 + 99)//100)
    else: quota=max(30, (n*25 + 99)//100)
    valid=[w for w in works if w.get('verified_aweme',w.get('read_status')!='仅发现链接，统计待补') and isinstance(w.get('digg_count'),(int,float))]
    ranked=sorted(valid,key=lambda w:w.get('digg_count',0),reverse=True)
    chosen={str(w.get('aweme_id')) for w in ranked[:quota] if w.get('aweme_id')}
    # Always keep the latest three and videos with verified play count >= 8000.
    recent=sorted([w for w in works if w.get('verified_aweme',w.get('read_status')!='仅发现链接，统计待补')],key=lambda w:str(w.get('publish_time') or ''),reverse=True)[:3]
    chosen.update(str(w.get('aweme_id')) for w in recent if w.get('aweme_id'))
    chosen.update(str(w.get('aweme_id')) for w in works if w.get('verified_aweme',w.get('read_status')!='仅发现链接，统计待补') and w.get('aweme_id') and isinstance(w.get('play_count'),(int,float)) and w.get('play_count')>=8000)
    # Keep unusually strong works even if their rank fell below the quota.
    if valid:
        vals=sorted(w.get('digg_count',0) for w in valid)
        median=vals[len(vals)//2]
        chosen.update(str(w.get('aweme_id')) for w in valid if w.get('digg_count',0)>=median*3 and w.get('aweme_id'))
    return chosen

def _standard_aweme_ids(works):
    """Select a wider, creator-aware download set for the 标准档.

    The quota grows for small creators and for creators whose overall reach is
    low.  This keeps a small account from being reduced to a few viral clips.
    Stable metadata is used for selection; CDN URLs are refreshed only when a
    selected item is downloaded.
    """
    valid=[w for w in works if w.get('verified_aweme',w.get('read_status')!='仅发现链接，统计待补') and w.get('aweme_id')]
    n=len(valid)
    if not n: return set()
    plays=sorted([float(w['play_count']) for w in valid if isinstance(w.get('play_count'),(int,float))])
    median=plays[len(plays)//2] if plays else 0
    # Small creators are almost completely archived. Larger creators use a
    # wider percentile than 精华, with a floor of 10 works.
    if n<=20: ratio=1.00
    elif n<=50: ratio=.80
    elif n<=100: ratio=.70
    elif n<=300: ratio=.60
    else: ratio=.50
    if median and median<8000: ratio=min(1.0, ratio+.15)
    quota=max(10, int(n*ratio+0.999))
    chosen=set()
    ranked_likes=sorted(valid,key=lambda w:(w.get('digg_count') if isinstance(w.get('digg_count'),(int,float)) else -1),reverse=True)
    ranked_plays=sorted(valid,key=lambda w:(w.get('play_count') if isinstance(w.get('play_count'),(int,float)) else -1),reverse=True)
    for row in ranked_likes[:quota]: chosen.add(str(row['aweme_id']))
    # Add a modest play-count band. For low-reach creators this threshold is
    # intentionally low; for normal creators keep the established 8000 floor.
    play_floor=max(1000, int(median*.50)) if median and median<8000 else 8000
    for row in valid:
        if isinstance(row.get('play_count'),(int,float)) and row['play_count']>=play_floor:
            chosen.add(str(row['aweme_id']))
    recent=sorted(valid,key=lambda w:str(w.get('publish_time') or ''),reverse=True)[:10]
    chosen.update(str(row['aweme_id']) for row in recent)
    # Guarantee at least ten items where the creator has ten or more works.
    if len(chosen)<min(10,n):
        for row in ranked_likes:
            chosen.add(str(row['aweme_id']))
            if len(chosen)>=min(10,n): break
    return chosen

def _harvest_worker(resume=False):
    state=_load_harvest_state();
    profile=str(state.get('download_profile') or ('essence' if state.get('essence_only') else 'all'))
    essence_only=profile=='essence'; state['download_profile']=profile; state['status']='运行中'; state['phase']='读取关注列表'; state['errors']=state.get('errors',[]); _save_harvest_state(state)
    try:
        # Keep the long-running harvest context authenticated without making
        # the login/status controls wait on its browser thread.
        try: sync_login_to_harvest()
        except Exception as auth_exc:
            state['errors'].append({'creator':'登录同步','error':str(auth_exc)}); _save_harvest_state(state)
        creators=state.get('creators') if resume and state.get('creators') else None
        if not creators:
            result=read_follows()
            if not result.get('ok'): raise RuntimeError(result.get('error') or '读取关注列表失败')
            creators=result.get('items') or []
            state['creators']=creators; state['creator_total']=len(creators); state['creator_done']=0
            state['creator_records']=[{'name':c.get('name',''),'url':c.get('url',''),'status':'待处理','phase':'等待处理','progress':0,'works':0,'downloaded':0,'errors':0} for c in creators]
        state['phase']='逐个采集主页'; _save_harvest_state(state)
        if not creators: raise RuntimeError('关注列表读取结果为空；请确认登录状态及关注页面正常显示')
        known={str(w.get('aweme_id')):w for w in state.get('works',[]) if w.get('aweme_id')}
        def collect(ctx):
            # Keep one download pool alive while the browser moves on to the
            # next creator. This makes collection and media download overlap.
            pool=ThreadPoolExecutor(max_workers=5,thread_name_prefix='douyin-download')
            pending=[]; manifests={}; stats={}
            def drain(block=False):
                nonlocal pending
                remaining=[]
                for item in pending:
                    idx,prev,fut=item
                    if not block and not fut.done(): remaining.append(item); continue
                    status,path=fut.result(); prev['download_status']=status; prev['local_path']=path if status in ('已下载','已存在') else ''
                    if status=='下载失败': prev['download_error']=path
                    d=stats.setdefault(idx,{'done':0,'failed':0})
                    d['done']+=1 if status in ('已下载','已存在') else 0
                    d['failed']+=1 if status in ('下载失败','无媒体地址') else 0
                pending=remaining
                for idx,d in stats.items():
                    rec=state.get('creator_records',[{}])[idx-1] if idx-1<len(state.get('creator_records',[])) else {}
                    _set_creator_record(state,idx-1,downloaded=d['done'],errors=d['failed'])
                if stats: state['works']=list(known.values()); _save_harvest_state(state)
            try:
              for idx,creator in enumerate(creators,1):
                _harvest_checkpoint(state)
                if idx<=int(state.get('creator_done',0)): continue
                _set_creator_record(state,idx-1,name=creator.get('name',''),url=creator.get('url',''),status='采集中',works=0,downloaded=0,errors=0)
                state['phase']=f"采集 {creator.get('name','')} ({idx}/{len(creators)})"; _save_harvest_state(state)
                try:
                    def creator_progress(value, phase):
                        _set_creator_record(state,idx-1,progress=int(value),phase=phase,status='采集中')
                        state['phase']=f"{creator.get('name','')}：{phase}（{int(value)}%）"
                        if int(value)%10==0 or value>=95: _save_harvest_state(state)
                    works,warning=_collect_creator_all(ctx,creator,creator_progress)
                    folder=HARVEST_DIR/(re.sub(r'[\\/:*?"<>|]','_',creator.get('name') or '未知博主')+'__'+hashlib.sha1(creator.get('url','').encode()).hexdigest()[:8]); folder.mkdir(parents=True,exist_ok=True)
                    # Persist the complete metadata/link snapshot before any
                    # media download starts. A download failure or restart
                    # must never discard the likes, play counts, and URLs
                    # already collected for this creator.
                    for rec in works:
                        prev=known.get(rec.get('aweme_id'),{})
                        prev.update(rec)
                        known[rec.get('aweme_id')]=prev
                    state['works']=list(known.values()); _save_harvest_state(state)
                    (folder/'主页作品.json').write_text(json.dumps(works,ensure_ascii=False,indent=2),encoding='utf-8')
                    done=0; failed=0
                    selected_ids=(_essence_aweme_ids(works) if profile=='essence' else _standard_aweme_ids(works) if profile=='standard' else {str(r.get('aweme_id')) for r in works})
                    manifests[idx]=(folder,works)
                    for rec in works:
                        _harvest_checkpoint(state)
                        prev=known.get(rec['aweme_id'],{}); prev.update(rec)
                        if not rec.get('verified_aweme',rec.get('read_status')!='仅发现链接，统计待补'):
                            prev['download_status']='未验证链接（仅保留链接）'; prev['local_path']=''; known[rec['aweme_id']]=prev; continue
                        if profile in ('essence','standard') and str(rec.get('aweme_id')) not in selected_ids:
                            prev['download_status']='未选择（筛选范围外）'; prev['selection_reason']=('精华' if profile=='essence' else '标准')+'规则暂未入选'; prev['local_path']=''; known[rec['aweme_id']]=prev; continue
                        if profile in ('essence','standard'):
                            prev['selection_reason']=('精华规则 v2 入选' if profile=='essence' else '标准规则入选（按博主数量/播放量放宽）')
                        known[rec['aweme_id']]=prev
                        pending.append((idx,prev,pool.submit(_download_aweme_http,prev,folder)))
                    state['works']=list(known.values()); state['creator_done']=idx
                    _set_creator_record(state,idx-1,status='采集完成（下载中）',phase='后台下载视频',progress=100,works=len(works),downloaded=0,errors=0)
                    drain(False)
                    if warning: state['errors'].append({'creator':creator.get('name'),'error':warning})
                    _save_harvest_state(state)
                except HarvestStopped:
                    _set_creator_record(state,idx-1,status='已停止',works=len(works) if 'works' in locals() else 0); _save_harvest_state(state); raise
                except Exception as exc:
                    _set_creator_record(state,idx-1,status='失败',errors=1)
                    state['errors'].append({'creator':creator.get('name'),'url':creator.get('url'),'error':str(exc)}); state['creator_done']=idx; _save_harvest_state(state)
              while pending:
                drain(True)
              for idx,(folder,works) in manifests.items():
                for row in works:
                    if row.get('aweme_id') in known: row.update(known[row['aweme_id']])
                folder.joinpath('主页作品.json').write_text(json.dumps(works,ensure_ascii=False,indent=2),encoding='utf-8')
                d=stats.get(idx,{'done':0,'failed':0})
                _set_creator_record(state,idx-1,status='完成（有告警）' if d['failed'] else '完成',phase='已完成',progress=100,works=len(works),downloaded=d['done'],errors=d['failed'])
              state['works']=list(known.values()); _save_harvest_state(state)
            finally:
              pool.shutdown(wait=True)
        def reconcile_essence(ctx):
            """Recheck the final metadata and download any newly selected items."""
            if profile not in ('essence','standard'): return
            groups={}
            for rec in known.values():
                groups.setdefault(rec.get('creator_url') or rec.get('creator') or '未知博主',[]).append(rec)
            state['phase']='复核精华清单并补下载'; _save_harvest_state(state)
            for group, items in groups.items():
                ids=_essence_aweme_ids(items) if profile=='essence' else _standard_aweme_ids(items)
                creator=items[0].get('creator') or '未知博主'
                folder=HARVEST_DIR/(re.sub(r'[\\/:*?"<>|]','_',creator)+'__'+hashlib.sha1(str(group).encode()).hexdigest()[:8]); folder.mkdir(parents=True,exist_ok=True)
                for rec in items:
                    if str(rec.get('aweme_id')) not in ids: continue
                    if rec.get('download_status') in ('已下载','已存在'): continue
                    status,path=_download_aweme(rec,folder,ctx)
                    rec['download_status']=status; rec['local_path']=path if status in ('已下载','已存在') else ''
                    if status=='下载失败': rec['download_error']=path
                state['works']=list(known.values()); _save_harvest_state(state)
        # Reuse the visible, authenticated Edge context. Launching a second
        # persistent Edge profile can be rejected by Edge's single-instance
        # behavior and leaves the web task looking unresponsive.
        interactive_browser_call(collect,timeout=86400)
        interactive_browser_call(reconcile_essence,timeout=86400)
        state['phase']='生成 Excel'; _save_harvest_state(state)
        state['workbook']=export_harvest_workbook(state)
        state['status']='完成'; state['phase']='完成'; _save_harvest_state(state)
    except HarvestStopped:
        state['status']='已停止'; state['phase']='已停止，已保存断点'; _save_harvest_state(state)
    except Exception as exc:
        state['status']='失败'; state['phase']='中断'; state['fatal_error']=str(exc); _save_harvest_state(state)

def export_harvest_workbook(state):
    node=r'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
    script=BASE/'outputs'/'01a19ce1-1e70-7e70-bcb4-5755707e4f14'/'export_harvest.mjs'
    if not script.exists(): raise RuntimeError(f'Excel 导出程序不存在：{script}')
    proc=subprocess.run([node,str(script),str(HARVEST_STATE),str(HARVEST_DIR/'关注视频汇总.xlsx')],capture_output=True,text=True,timeout=3600)
    if proc.returncode: raise RuntimeError('Excel 导出失败：'+(proc.stderr or proc.stdout)[-3000:])
    return str(HARVEST_DIR/'关注视频汇总.xlsx')

def export_creators_workbook():
    node=r'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
    script=BASE/'outputs'/'01a19ce1-1e70-7e70-bcb4-5755707e4f14'/'export_creators.mjs'
    out=ROOT/'全部博主主页.xlsx'
    if not script.exists(): raise RuntimeError(f'博主导出程序不存在：{script}')
    proc=subprocess.run([node,str(script),str(CREATORS),str(out)],capture_output=True,text=True,timeout=300)
    if proc.returncode: raise RuntimeError('博主 Excel 导出失败：'+(proc.stderr or proc.stdout)[-3000:])
    return out

def clear_harvest_data():
    global HARVEST_CLEAR_THREAD, HARVEST_CLEAR_STATUS
    with HARVEST_CLEAR_LOCK:
        if HARVEST_THREAD is not None and HARVEST_THREAD.is_alive():
            return {'ok':False,'error':'采集任务仍在运行，请先暂停或停止后再清空。'}
        if HARVEST_CLEAR_STATUS.get('status')=='运行中':
            return {'ok':True,'message':'清空任务已经在运行。','status':'运行中'}
        stamp=time.strftime('%Y%m%d_%H%M%S')
        backup=LIB/'采集归档备份'/f'关注归档_{stamp}'
        HARVEST_CLEAR_STATUS={'status':'运行中','message':'正在备份并清空采集内容。','backup':str(backup),'removed':0}
        def worker():
            global HARVEST_CLEAR_STATUS
            try:
                backup.mkdir(parents=True,exist_ok=True)
                removed=0
                for child in HARVEST_DIR.iterdir():
                    target=backup/child.name
                    if child.is_dir(): shutil.copytree(child,target,dirs_exist_ok=True); shutil.rmtree(child)
                    else: shutil.copy2(child,target); child.unlink()
                    removed+=1
                empty={'status':'空闲','phase':'尚未开始','creators':[],'creator_records':[],'creator_done':0,'creator_total':0,'works':[],'errors':[],'workbook':'','fatal_error':'','updated_at':time.strftime('%Y-%m-%d %H:%M:%S')}
                _save_harvest_state(empty)
                HARVEST_CLEAR_STATUS={'status':'完成','message':f'已备份并清空采集内容，共处理 {removed} 项。','backup':str(backup),'removed':removed}
            except Exception as exc:
                HARVEST_CLEAR_STATUS={'status':'失败','message':f'清空失败：{exc}','backup':str(backup),'removed':0}
        HARVEST_CLEAR_THREAD=threading.Thread(target=worker,daemon=True); HARVEST_CLEAR_THREAD.start()
        return {'ok':True,'status':'运行中','backup':str(backup),'message':'已开始备份并清空采集内容，请稍候刷新进度。'}

def harvest_clear_status():
    with HARVEST_CLEAR_LOCK: return dict(HARVEST_CLEAR_STATUS)

def repair_duplicate_archives():
    """Quarantine works whose recorded author differs from the folder owner."""
    stamp=time.strftime('%Y%m%d_%H%M%S')
    quarantine=LIB/'虚拟回收站'/f'归属修复_{stamp}'
    report=[]; moved=0; changed=0
    for manifest in HARVEST_DIR.glob('*/主页作品.json'):
        try: rows=json.loads(manifest.read_text(encoding='utf-8'))
        except Exception: continue
        if not isinstance(rows,list): continue
        folder_owner=_creator_id_from_url(str(rows[0].get('creator_url') or '')) if rows else ''
        if not folder_owner: continue
        keep=[]; bad=[]
        for row in rows:
            author=str(row.get('creator_sec_uid') or '').strip()
            if author and author!=folder_owner: bad.append(row)
            else: keep.append(row)
        if not bad: continue
        changed+=1
        target_dir=quarantine/manifest.parent.name
        target_dir.mkdir(parents=True,exist_ok=True)
        for row in bad:
            aid=str(row.get('aweme_id') or '')
            if not aid: continue
            for candidate in manifest.parent.glob(aid+'.*'):
                try:
                    dest=target_dir/candidate.name
                    if dest.exists(): dest=target_dir/(candidate.stem+'_'+uuid.uuid4().hex[:6]+candidate.suffix)
                    shutil.move(str(candidate),str(dest)); moved+=1
                    report.append({'folder':manifest.parent.name,'aweme_id':aid,'source':str(candidate),'quarantine':str(dest),'creator':row.get('creator',''),'actual_author_sec_uid':author})
                except Exception as exc:
                    report.append({'folder':manifest.parent.name,'aweme_id':aid,'error':str(exc)})
        manifest.write_text(json.dumps(keep,ensure_ascii=False,indent=2),encoding='utf-8')
    quarantine.mkdir(parents=True,exist_ok=True)
    (quarantine/'修复报告.json').write_text(json.dumps({'created_at':time.strftime('%Y-%m-%d %H:%M:%S'),'folders_changed':changed,'files_moved':moved,'items':report},ensure_ascii=False,indent=2),encoding='utf-8')
    return {'ok':True,'message':f'归属修复完成：检查并更新 {changed} 个文件夹，移入虚拟回收站 {moved} 个文件。','quarantine':str(quarantine),'folders_changed':changed,'files_moved':moved}

def start_harvest(options=None):
    global HARVEST_THREAD
    with HARVEST_LOCK:
        state=_load_harvest_state()
        if options is not None:
            profile=str(options.get('download_profile') or ('essence' if options.get('essence_only') else 'all'))
            if profile not in ('essence','standard','all'): profile='essence'
            state['download_profile']=profile
            state['essence_only']=profile=='essence'
            state['harvest_mode']=str(options.get('mode') or 'incremental')
            if state['harvest_mode'] in ('incremental','standard','full'):
                # A new web run rescans all creators while retaining works,
                # metrics and download history. Existing files are skipped.
                state['creator_done']=0
                state['creator_records']=[dict(x,status='待更新',errors=0) for x in state.get('creator_records',[])]
                state['snapshot_requested']=True
        if state.get('status')=='运行中' and HARVEST_THREAD is not None and HARVEST_THREAD.is_alive(): return {'ok':True,'message':'采集任务已经在运行。'}
        HARVEST_PAUSE.clear(); HARVEST_STOP.clear(); state['status']='运行中'; state['fatal_error']=''; _save_harvest_state(state)
        HARVEST_THREAD=threading.Thread(target=_harvest_worker,args=(bool(state.get('creators') and state.get('creator_done')) ,),daemon=True); HARVEST_THREAD.start()
    return {'ok':True,'message':'已开始读取账号关注列表，请保持网页登录窗口开启。'}

def control_harvest(action):
    global HARVEST_THREAD
    state=_load_harvest_state()
    if action=='pause' and state.get('status')=='运行中':
        HARVEST_PAUSE.set(); state['status']='暂停'; state['phase']='暂停请求已发送'; _save_harvest_state(state); return {'ok':True,'message':'已请求暂停，当前视频完成后保存断点。'}
    if action=='resume' and (state.get('status') in ('暂停','已停止','失败') or (state.get('status')=='运行中' and (HARVEST_THREAD is None or not HARVEST_THREAD.is_alive()))):
        HARVEST_PAUSE.clear(); HARVEST_STOP.clear(); state['status']='运行中'; _save_harvest_state(state); HARVEST_THREAD=threading.Thread(target=_harvest_worker,args=(True,),daemon=True); HARVEST_THREAD.start(); return {'ok':True,'message':'已继续采集。'}
    if action=='stop' and state.get('status')=='运行中':
        HARVEST_STOP.set(); HARVEST_PAUSE.clear(); return {'ok':True,'message':'已请求停止，当前请求完成后保存断点。'}
    if action=='retry':
        state['errors']=[]; return control_harvest('resume')
    return {'ok':True,'message':f'当前状态为 {state.get("status","空闲")}，没有执行该操作。'}

def save_creator_items(items):
    current=read_creators(); by_url={x.get('url'):x for x in current}; added=0
    for raw in items or []:
        name=str(raw.get('name') or '').strip(); url=str(raw.get('url') or '').strip()
        if not name or not url: continue
        url=url.replace('https://www.douyin.com//www.douyin.com/','https://www.douyin.com/')
        if url in by_url: continue
        item={'id':'creator_'+uuid.uuid4().hex[:10],'name':name,'url':url,'created_at':time.strftime('%Y-%m-%dT%H:%M:%S')}; current.append(item); by_url[url]=item; added+=1
    write_creators(current); return current,added

def discover_video_urls(homepage_url, creator=None):
    def work(ctx):
        page=ctx.pages[0] if ctx.pages else ctx.new_page(); found={}
        def on_response(response):
            u=response.url.lower()
            if not any(x in u for x in ('aweme','post','work','item_list')): return
            try:
                payload=response.json()
                for aweme in _walk_awemes(payload):
                    if creator and not _aweme_belongs_to_creator(aweme,creator): continue
                    rec=_aweme_record(aweme,creator or {'name':'','url':homepage_url})
                    if rec.get('aweme_id'): found[rec['aweme_id']]=rec
            except Exception: pass
        page.on('response',on_response)
        page.goto(homepage_url,wait_until='domcontentloaded',timeout=30000); page.bring_to_front(); page.wait_for_timeout(4000)
        for label in ('作品','视频','Posts'):
            try:
                loc=page.get_by_text(label,exact=True).last
                if loc.is_visible(timeout=700): loc.click(); page.wait_for_timeout(2500); break
            except Exception: pass
        seen=set(); unchanged=0
        for _ in range(100):
            links=page.evaluate("""() => Array.from(document.querySelectorAll('a[href*="/video/"],a[href*="/note/"]')).map(a=>a.href||a.getAttribute('href')||'')""") or []
            if not links:
                html=page.content()
                links=re.findall(r'(?:https?:\\/\\/[^" ]+)?\\/video\\/[0-9]+',html)
            before=len(seen)
            for href in links:
                href=str(href).replace('\\/','/')
                m=re.search(r'(?:https?://(?:www\\.)?douyin\\.com)?/video/([0-9]+)',href)
                if m:
                    aid=m.group(1); seen.add('https://www.douyin.com/video/'+aid)
                    found.setdefault(aid,{'creator':(creator or {}).get('name',''),'creator_url':homepage_url,'aweme_id':aid,'url':'https://www.douyin.com/video/'+aid,'title':'','publish_time':'','play_count':None,'digg_count':None,'collect_count':None,'comment_count':None,'share_count':None,'duration_ms':None,'media_urls':[],'verified_aweme':False,'read_status':'仅发现链接，统计待补'})
            unchanged=unchanged+1 if len(seen)==before else 0
            if unchanged>=10: break
            page.evaluate("""() => { const e=document.scrollingElement; window.scrollBy(0,1200); if(e)e.scrollTop=Math.min(e.scrollTop+1400,e.scrollHeight); }""")
            page.wait_for_timeout(900)
        page.remove_listener('response',on_response)
        records=list(found.values())
        if records: return records
        return [{'url':u,'aweme_id':u.rsplit('/',1)[-1],'media_urls':[]} for u in sorted(seen)]
    records=browser_call(work,timeout=240)
    if not records: raise RuntimeError('没有从博主主页发现作品链接，请确认已登录且主页可以正常显示作品')
    return records

def make_task(item, data):
    tid='debug_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]; path=TASKS/(tid+'.json')
    records=discover_video_urls(item['url'],item) if '/user/' in item['url'] else [{'url':item['url'],'aweme_id':item['url'].rstrip('/').rsplit('/',1)[-1],'media_urls':[]}]
    urls=[x.get('url') for x in records if x.get('url')]
    task={'task_id':tid,'creator_name':item['name'],'homepage_url':item['url'],'video_urls':urls,'video_items':records,'start_date':data.get('start_date',''),'download_dir':str(LIB/'待导入'/item['name']),'cookie_mode':data.get('cookie_mode',''),'cookie_file':data.get('cookie_file',''),'status':'待下载'}
    path.write_text(json.dumps(task,ensure_ascii=False,indent=2),encoding='utf-8'); return tid,path

def _bulk_download_worker(batch_id, items, data):
    with BATCH_LOCK:
        batch=BATCHES.get(batch_id)
        if not batch: return
        batch['status']='运行中'; batch['message']='正在逐个发现博主作品，页面可继续操作'
    try:
        if not str(data.get('cookie_mode') or '').strip(): data['cookie_mode']='edge'
        data=prepare_download_data(data)
    except Exception as exc:
        with BATCH_LOCK:
            batch=BATCHES.get(batch_id)
            if batch:
                for entry in batch.get('items',[]): entry.update({'status':'失败','error':f'登录状态准备失败：{exc}'})
                batch['status']='失败'; batch['message']=f'登录状态准备失败：{exc}'
        return
    current,_=save_creator_items(items); wanted={str(x.get('url')) for x in items};
    for item in current:
        if item.get('url') not in wanted: continue
        entry=next((x for x in BATCHES[batch_id]['items'] if x.get('url')==item.get('url')),None)
        try:
            with BATCH_LOCK:
                entry['status']='发现作品'; entry['updated_at']=time.strftime('%Y-%m-%d %H:%M:%S')
            tid,path=make_task(item,data); launch_task(tid,path)
            with BATCH_LOCK:
                entry.update({'status':'已启动','task_id':tid,'task_path':str(path),'updated_at':time.strftime('%Y-%m-%d %H:%M:%S')})
        except Exception as exc:
            with BATCH_LOCK:
                entry.update({'status':'失败','error':str(exc),'updated_at':time.strftime('%Y-%m-%d %H:%M:%S')})
    with BATCH_LOCK:
        batch=BATCHES[batch_id]; failures=sum(1 for x in batch.get('items',[]) if x.get('status')=='失败'); batch['status']='完成（有失败）' if failures else '完成'; batch['message']=f'批量任务处理完成，{failures} 位博主失败'; batch['finished_at']=time.strftime('%Y-%m-%d %H:%M:%S')

def create_bulk_download(items, data):
    batch_id='batch_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]
    entries=[{'name':str(x.get('name') or ''),'url':str(x.get('url') or ''),'status':'排队中'} for x in items or []]
    with BATCH_LOCK:
        BATCHES[batch_id]={'batch_id':batch_id,'status':'准备中','message':'已创建批量任务','items':entries,'created_at':time.strftime('%Y-%m-%d %H:%M:%S')}
    threading.Thread(target=_bulk_download_worker,args=(batch_id,list(items or []),dict(data)),daemon=True).start()
    return batch_id

def bulk_download_status(batch_id):
    with BATCH_LOCK:
        batch=BATCHES.get(batch_id)
        if not batch: return {'error':'批量任务不存在'}
        result=json.loads(json.dumps(batch,ensure_ascii=False))
    entries=result.get('items',[]); done=sum(1 for x in entries if x.get('status') in ('已启动','失败')); running=sum(1 for x in entries if x.get('status') in ('排队中','发现作品'))
    result['summary']=f"{done}/{len(entries)} 个博主已处理" + (f"，{running} 个处理中" if running else '')
    return result

def prepare_download_data(data):
    mode=str(data.get('cookie_mode') or '').lower()
    if mode in ('edge','chrome','firefox','chromium'):
        def get_cookies(ctx): return ctx.cookies()
        cookies=interactive_browser_call(get_cookies, timeout=30)
        cookie_path=ROOT/'douyin_login_cookies.txt'
        with cookie_path.open('w',encoding='utf-8',newline='\n') as f:
            f.write('# Netscape HTTP Cookie File\n')
            for c in cookies:
                domain=c.get('domain',''); include='TRUE' if domain.startswith('.') else 'FALSE'; path=c.get('path','/')
                secure='TRUE' if c.get('secure') else 'FALSE'; raw_expiry=c.get('expires'); expiry=str(int(raw_expiry)) if raw_expiry and float(raw_expiry)>0 else '0'; name=c.get('name',''); value=c.get('value','')
                f.write('\t'.join([domain,include,path,secure,expiry,name,value])+'\n')
        if not cookies: raise RuntimeError('扫码登录窗口中没有可用登录状态，请先完成登录')
        data=dict(data); data['cookie_mode']='file'; data['cookie_file']=str(cookie_path)
    return data

def launch_task(tid,path):
    if JOBS.get(tid,{}).get('status')=='运行中': return
    log=path.with_suffix('.log').open('a',encoding='utf-8'); p=subprocess.Popen([sys.executable,str(Path(__file__).with_name('download_task.py')),str(path)],cwd=str(BASE),stdout=log,stderr=subprocess.STDOUT); JOBS[tid]={'task_id':tid,'status':'运行中','pid':p.pid,'log':str(path.with_suffix('.log'))}
    def watch():
        code=p.wait(); log.close(); JOBS[tid]['status']='完成' if code==0 else '失败'; JOBS[tid]['returncode']=code
    threading.Thread(target=watch,daemon=True).start()

def _admin_ok(h):
    token=h.headers.get('X-Admin-Token','')
    if token in DOWNLOADER_SESSIONS and time.time()-DOWNLOADER_SESSIONS[token] < 12*3600:
        DOWNLOADER_SESSIONS[token]=time.time(); return True
    return False

def _send_file(h,path,download=False):
    path=Path(path)
    if not path.is_file(): h.send_error(404); return
    data=path.read_bytes(); h.send_response(200); h.send_header('Content-Type','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'); h.send_header('Content-Length',str(len(data)))
    if download:
        # Keep the HTTP header ASCII-safe on Windows; the workbook itself keeps
        # the Chinese sheet and column names.
        h.send_header('Content-Disposition','attachment; filename="creators.xlsx"')
    h.end_headers(); h.wfile.write(data)

def _send_text_file(h,path,content_type='text/plain; charset=utf-8'):
    path=Path(path)
    if not path.is_file(): h.send_error(404); return
    data=path.read_bytes(); h.send_response(200); h.send_header('Content-Type',content_type); h.send_header('Content-Length',str(len(data))); h.send_header('Content-Disposition','attachment; filename="download-backlog.csv"'); h.end_headers(); h.wfile.write(data)

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def body(self):
        n=int(self.headers.get('Content-Length','0')); return json.loads(self.rfile.read(n) or b'{}')
    def do_GET(self):
        u=urlparse(self.path)
        if u.path=='/favicon.ico':
            self.send_response(204); self.end_headers(); return
        if u.path=='/':
            b=PAGE.encode(); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b); return
        if u.path=='/api/creators': reply(self,read_creators()); return
        if u.path=='/api/status': reply(self,list(JOBS.values())); return
        if u.path=='/api/bulk-download-status': reply(self,bulk_download_status(parse_qs(u.query).get('id',[''])[0])); return
        if u.path=='/api/login-status': reply(self,login_status()); return
        if u.path=='/api/follows': reply(self,read_follows()); return
        if u.path=='/api/fans': reply(self,read_fans()); return
        if u.path=='/api/page-debug': reply(self,page_debug()); return
        if u.path=='/api/harvest-status': reply(self,harvest_status()); return
        if u.path=='/api/harvest-clear-status': reply(self,harvest_clear_status()); return
        if u.path=='/api/harvest-backlog-status': reply(self,backlog_status()); return
        if u.path=='/api/harvest-works': reply(self,harvest_works(parse_qs(u.query))); return
        if u.path=='/api/harvest-file': _send_file(self,HARVEST_DIR/'关注视频汇总.xlsx'); return
        if u.path=='/api/harvest-backlog-file': _send_text_file(self,HARVEST_DIR/'待下载清单.csv','text/csv; charset=utf-8'); return
        if u.path=='/api/creators-file': _send_file(self,ROOT/'全部博主主页.xlsx',download=True); return
        reply(self,{'error':'未找到'},404)
    def do_POST(self):
        u=urlparse(self.path); d=self.body()
        if u.path=='/api/admin-login':
            if secrets.compare_digest(str(d.get('password','')),str(DOWNLOADER_CFG.get('management_password',''))):
                token=secrets.token_urlsafe(24); DOWNLOADER_SESSIONS[token]=time.time(); reply(self,{'ok':True,'token':token}); return
            reply(self,{'error':'管理密码错误'},401); return
        if u.path=='/api/login': reply(self,open_login()); return
        if u.path in ('/api/harvest','/api/harvest-control','/api/harvest-clear','/api/harvest-repair-duplicates','/api/harvest-backlog-download','/api/creators/bulk','/api/bulk-download','/api/creators','/api/creators-export','/api/creators-clear','/api/tasks','/api/run') and not _admin_ok(self):
            reply(self,{'error':'需要管理模式，请先输入管理密码'},403); return
        if u.path=='/api/harvest': reply(self,start_harvest(d)); return
        if u.path=='/api/harvest-control': reply(self,control_harvest(str(d.get('action') or ''))); return
        if u.path=='/api/harvest-clear': reply(self,clear_harvest_data()); return
        if u.path=='/api/harvest-repair-duplicates':
            try: reply(self,repair_duplicate_archives())
            except Exception as e: reply(self,{'error':str(e)},500)
            return
        if u.path=='/api/harvest-backlog-download': reply(self,start_backlog_download()); return
        if u.path=='/api/creators/bulk':
            _,added=save_creator_items(d.get('items')); reply(self,{'ok':True,'added':added,'message':f'已保存 {added} 个新博主。'}); return
        if u.path=='/api/creators-export':
            try:
                out=export_creators_workbook()
                reply(self,{'ok':True,'url':'/api/creators-file','message':f'已导出 {len(read_creators())} 个博主。'})
            except Exception as e: reply(self,{'error':str(e)},500)
            return
        if u.path=='/api/creators-clear':
            removed,backup=clear_creators()
            reply(self,{'ok':True,'removed':removed,'backup':backup,'message':f'已清空 {removed} 个博主，清空前备份已保存。'})
            return
        if u.path=='/api/bulk-download':
            items=d.get('items') or []
            if not items: reply(self,{'error':'请先勾选博主'},400); return
            batch_id=create_bulk_download(items,d)
            reply(self,{'ok':True,'batch_id':batch_id,'message':f'已创建批量任务（{len(items)} 位博主），正在后台发现作品。'}); return
        if u.path=='/api/creators':
            name=str(d.get('name') or '').strip(); url=str(d.get('url') or '').strip()
            if not name or not url: reply(self,{'error':'博主名称和主页链接都要填写'},400); return
            if not re.match(r'https?://',url): reply(self,{'error':'主页链接应以 http:// 或 https:// 开头'},400); return
            items=read_creators(); item={'id':'creator_'+uuid.uuid4().hex[:10],'name':name,'url':url,'created_at':time.strftime('%Y-%m-%dT%H:%M:%S')}; items.append(item); write_creators(items); reply(self,{'ok':True,'message':'博主已保存','creator':item}); return
        if u.path=='/api/tasks':
            try: d=prepare_download_data(d)
            except Exception as e: reply(self,{'error':f'无法准备登录状态：{e}'},400); return
            item=next((x for x in read_creators() if x['id']==d.get('creator_id')),None)
            if not item: reply(self,{'error':'请先选择博主'},400); return
            try: task_id,path=make_task(item,d)
            except Exception as e: reply(self,{'error':f'发现博主作品失败：{e}'},400); return
            reply(self,{'ok':True,'task_id':task_id,'message':'已发现作品并创建任务，正在启动'}); return
        if u.path=='/api/run':
            tid=str(d.get('task_id') or ''); path=(TASKS/(tid+'.json')).resolve()
            if not re.fullmatch(r'debug_[A-Za-z0-9_]+',tid) or not path.exists(): reply(self,{'error':'任务不存在'},400); return
            if JOBS.get(tid,{}).get('status')=='运行中': reply(self,{'message':'任务已在运行'}); return
            log=path.with_suffix('.log').open('a',encoding='utf-8'); p=subprocess.Popen([sys.executable,str(Path(__file__).with_name('download_task.py')),str(path)],cwd=str(BASE),stdout=log,stderr=subprocess.STDOUT); JOBS[tid]={'task_id':tid,'status':'运行中','pid':p.pid,'log':str(path.with_suffix('.log'))}
            def watch():
                code=p.wait(); log.close(); JOBS[tid]['status']='完成' if code==0 else '失败'; JOBS[tid]['returncode']=code
            threading.Thread(target=watch,daemon=True).start(); reply(self,{'ok':True,'message':'下载已启动'}); return
        reply(self,{'error':'未找到'},404)

if __name__=='__main__':
    print(f'独立下载器: http://127.0.0.1:{PORT}')
    ThreadingHTTPServer((HOST,PORT),Handler).serve_forever()
