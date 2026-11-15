"""Local video catalog. All mutations are journaled; configured legacy imports are explicit."""
from __future__ import annotations
import argparse, concurrent.futures as cf, csv, datetime as dt, hashlib, html, io
import json, math, os, re, shutil, sqlite3, subprocess, sys, time, urllib.request, urllib.parse, uuid
from pathlib import Path
from collections import defaultdict, Counter
from PIL import Image, ImageOps
import numpy as np

BASE = Path(__file__).resolve().parent.parent
LIB = BASE / '本地视频库'
SOURCE = BASE / '下载的视频'
ALLOWED = [SOURCE / '抖音下载', SOURCE / '抖音下载2']
LEGACY_ROOT = SOURCE / '快斗下载器' / '快抖下载器'
EXCLUDED = []
DB = LIB / '索引' / 'library.sqlite3'
FFMPEG = Path('C:/Users/Administrator/AppData/Local/Programs/Python/Python313/Lib/site-packages/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v9.0.2.exe')
NOW = lambda: dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec='seconds')
DCT = np.cos(np.pi*np.arange(8)[:,None]*(2*np.arange(32)[None,:]+1)/64)

def digest(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(4*1024*1024), b''): h.update(b)
    return h.hexdigest()

def inside(p, root): return Path(p).resolve().is_relative_to(root.resolve())

def safe(s, n=65):
    s = re.sub(r'[\x00-\x1f<>:"/\\|?*]', '_', str(s)).strip(' .')[:n].rstrip(' .') or '未命名'
    if re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])', s): s = '_' + s
    return s

def connect():
    for name in ['博主','待确认','待导入','虚拟回收站','其他资料','索引','报告','操作记录','报告/缩略图']:
        (LIB/name).mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY,created_at TEXT,scope_json TEXT);
    CREATE TABLE IF NOT EXISTS creators(id TEXT PRIMARY KEY,name TEXT,platform_id TEXT,identity_status TEXT,aliases_json TEXT);
    CREATE TABLE IF NOT EXISTS works(id TEXT PRIMARY KEY,creator_id TEXT REFERENCES creators(id),title TEXT,platform_id TEXT,url TEXT,published_at TEXT,tags_json TEXT,identity_status TEXT);
    CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY,batch_id TEXT REFERENCES batches(id),work_id TEXT REFERENCES works(id),creator_id TEXT REFERENCES creators(id),original_path TEXT,current_path TEXT UNIQUE,size INTEGER,mtime_ns INTEGER,sha256 TEXT,extension TEXT,kind TEXT,duplicate_of TEXT REFERENCES files(id),target_path TEXT,status TEXT,author_basis TEXT,association_status TEXT,backup_status TEXT DEFAULT '未验证独立备份');
    CREATE INDEX IF NOT EXISTS files_hash ON files(sha256);
    CREATE INDEX IF NOT EXISTS works_platform ON works(platform_id);
    CREATE TABLE IF NOT EXISTS media(file_id TEXT PRIMARY KEY REFERENCES files(id),duration REAL,width INTEGER,height INTEGER,codec TEXT,status TEXT,method TEXT,hashes_json TEXT,thumbnail TEXT,detail TEXT,checked_at TEXT);
    CREATE TABLE IF NOT EXISTS metrics(id INTEGER PRIMARY KEY,work_id TEXT REFERENCES works(id),metric TEXT,value REAL,observed_at TEXT,recorded_at TEXT,source TEXT,source_file TEXT,source_locator TEXT,value_kind TEXT,confidence TEXT,evidence TEXT);
    CREATE TABLE IF NOT EXISTS enrichment(id INTEGER PRIMARY KEY,work_id TEXT REFERENCES works(id),attempted_at TEXT,url TEXT,status TEXT,http_status INTEGER,detail TEXT);
    CREATE TABLE IF NOT EXISTS moves(id INTEGER PRIMARY KEY,file_id TEXT REFERENCES files(id),batch_id TEXT,src TEXT,dst TEXT,sha256 TEXT,state TEXT,created_at TEXT,completed_at TEXT,error TEXT);
    CREATE TABLE IF NOT EXISTS similar(a TEXT REFERENCES files(id),b TEXT REFERENCES files(id),reason TEXT,score REAL,decision TEXT DEFAULT '待确认',note TEXT,PRIMARY KEY(a,b));
    CREATE TABLE IF NOT EXISTS review_log(id INTEGER PRIMARY KEY,created_at TEXT,object_type TEXT,object_id TEXT,old_json TEXT,new_json TEXT);
    CREATE TABLE IF NOT EXISTS feedback(id TEXT PRIMARY KEY,work_id TEXT REFERENCES works(id),event_at TEXT,event_type TEXT,watch_ms INTEGER,session_id TEXT,recommender_version TEXT,extra_json TEXT);
    CREATE VIEW IF NOT EXISTS video_index AS SELECT f.id file_id,w.id work_id,c.name creator,w.title,w.platform_id,w.url,f.current_path,f.sha256,f.size,f.duplicate_of,f.status,m.duration,m.width,m.height,m.status media_status FROM files f LEFT JOIN works w ON f.work_id=w.id LEFT JOIN creators c ON f.creator_id=c.id LEFT JOIN media m ON m.file_id=f.id WHERE f.extension='.mp4';
    ''')
    if 'filename_platform_id' not in [r[1] for r in c.execute('PRAGMA table_info(files)')]:
        c.execute('ALTER TABLE files ADD COLUMN filename_platform_id TEXT')
    c.commit()
    return c

def write_json(p, data):
    p = Path(p); p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_suffix(p.suffix+'.tmp'); tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8'); tmp.replace(p)

def backup(c, name):
    dest=LIB/'索引'/'备份'/name; dest.parent.mkdir(exist_ok=True)
    target=sqlite3.connect(dest)
    try: c.backup(target)
    finally: target.close()

def scan(c, include_legacy=False):
    roots=ALLOWED+[LIB/'待导入'] + ([LEGACY_ROOT] if include_legacy else [])
    paths=sorted([p for root in roots if root.exists() for p in root.rglob('*') if p.is_file()],key=lambda p:str(p))
    known={r['current_path']:r for r in c.execute('SELECT * FROM files')}
    todo=[]
    for p in paths:
        st=p.stat(); old=known.get(str(p))
        if old:
            if old['size']!=st.st_size or old['mtime_ns']!=st.st_mtime_ns: raise RuntimeError('已入库文件发生改变，请先核查：'+str(p))
        else: todo.append(p)
    if not todo:
        print('没有新增文件',flush=True); return
    batch=dt.datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]
    c.execute('INSERT INTO batches VALUES(?,?,?)',(batch,NOW(),json.dumps([str(x) for x in roots],ensure_ascii=False))); c.commit()
    def inspect(p):
        before=p.stat(); sha=digest(p); after=p.stat()
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns): raise RuntimeError('扫描期间文件发生变化：'+str(p))
        return p,after,sha
    manifest=[]
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        for i,(p,st,sha) in enumerate(ex.map(inspect,todo),1):
            fid='F'+uuid.uuid4().hex[:16]; ext=p.suffix.lower(); kind='video' if ext=='.mp4' else 'image' if ext in ['.jpg','.jpeg','.png'] else 'incomplete' if ext=='.crdownload' else 'other'
            rel=p.relative_to(SOURCE) if inside(p,SOURCE) else None
            name=None
            if rel and rel.parts[0]=='快斗下载器' and len(rel.parts)>3: name=re.sub(r'_\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d$','',rel.parts[2])
            elif rel and rel.parts[0]=='抖音下载2' and len(rel.parts)>2: name=rel.parts[1]
            elif rel and rel.parts[0]=='抖音下载' and len(rel.parts)>2:
                name=rel.parts[2] if rel.parts[1]=='抖音' and len(rel.parts)>3 else rel.parts[1]
            if name in ['抖音','12','我收藏的作品']: name=None
            if rel and rel.parts[0]=='快斗下载器' and len(rel.parts)>2 and rel.parts[2].startswith(('我喜欢的作品_','我收藏的作品_')):
                name=None
            incoming=p.relative_to(LIB/'待导入') if inside(p,LIB/'待导入') else None
            if incoming and len(incoming.parts)>1: name=incoming.parts[0]
            cid=None
            if name:
                # A folder label is provisional identity, never proof that same nicknames are same account.
                key=str(p.parent.relative_to(SOURCE)) if rel else str(p.parent)
                cid='C'+hashlib.sha256(key.encode()).hexdigest()[:12]
                c.execute('INSERT OR IGNORE INTO creators VALUES(?,?,NULL,?,?)',(cid,name,'待核实账号身份',json.dumps([name],ensure_ascii=False)))
            wid=None
            if kind=='video':
                match=re.search(r'(?:^|_)(\d{18,20})$',p.stem); platform=match.group(1) if match else None
                title=re.sub(r'^\d+[ _]+','',p.stem)
                if platform: title=re.sub(r'_?'+platform+'$','',title)
                if title in ['douyin_video','douyin','video']: title='标题未知'
                old=c.execute('SELECT id,creator_id FROM works WHERE platform_id=?',(platform,)).fetchone() if platform else None
                if old and old['creator_id']!=cid:
                    # Conflicting folder authors remain separate until reviewed.
                    old=None
                same=c.execute("SELECT work_id FROM files WHERE sha256=? AND extension='.mp4' AND creator_id IS ? LIMIT 1",(sha,cid)).fetchone() if not platform else None
                wid=old['id'] if old else same['work_id'] if same else 'W'+uuid.uuid4().hex[:16]
                c.execute('INSERT OR IGNORE INTO works VALUES(?,?,?,?,?,NULL,?,?)',(wid,cid,title or '标题未知',platform,'https://www.douyin.com/video/'+platform if platform else None,json.dumps(re.findall(r'#([^#]+)',title),ensure_ascii=False),'作品ID来自文件名' if platform else '仅本地文件'))
            c.execute('''INSERT INTO files(id,batch_id,work_id,creator_id,original_path,current_path,size,mtime_ns,sha256,extension,kind,status,author_basis,association_status,filename_platform_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(fid,batch,wid,cid,str(p),str(p),st.st_size,st.st_mtime_ns,sha,ext,kind,'已清点','目录名推定，未核实平台账号' if cid else '无法可靠识别','作品文件' if wid else '未关联',platform if kind=='video' else None))
            manifest.append(dict(id=fid,path=str(p),size=st.st_size,mtime_ns=st.st_mtime_ns,sha256=sha))
            if i%100==0: c.commit(); print('清点',i,'/',len(todo),flush=True)
    # Associate assets only when their stems exactly match a video in the same source folder.
    for r in c.execute("SELECT * FROM files WHERE kind='image' AND work_id IS NULL").fetchall():
        candidates=c.execute("SELECT work_id,original_path FROM files WHERE kind='video' AND creator_id IS ?",(r['creator_id'],)).fetchall()
        matches=[v for v in candidates if Path(v['original_path']).parent==Path(r['original_path']).parent and Path(v['original_path']).stem==Path(r['original_path']).stem]
        if len(matches)==1: c.execute("UPDATE files SET work_id=?,association_status='同目录同名关联' WHERE id=?",(matches[0]['work_id'],r['id']))
    c.commit(); write_json(LIB/'操作记录'/('原始清单_'+batch+'.json'),manifest)
    backup(c,'清点后_'+batch+'.sqlite3')
    print('清点完成',len(todo),'批次',batch,flush=True)

def media_one(r):
    p=Path(r['current_path']); result=dict(file_id=r['id'],duration=None,width=None,height=None,codec=None,status='未检查',method='none',hashes_json='[]',thumbnail=None,detail='',checked_at=NOW())
    if r['kind']=='image':
        try:
            with Image.open(p) as im: result.update(width=im.width,height=im.height); im.verify()
            result.update(status='图片可读取',method='Pillow.verify')
        except Exception as e: result.update(status='图片异常',detail=str(e))
        return result
    if r['kind']=='incomplete': result.update(status='下载未完成',detail='扩展名为.crdownload'); return result
    if r['kind']!='video': result.update(status='未执行或解析',detail='保留原文件，程序不执行'); return result
    result['method']='FFmpeg头信息＋关键帧抽样，前24秒每3秒取样，最多8帧；非完整解码'
    try:
        args=[str(FFMPEG),'-hide_banner','-nostdin','-threads','1','-skip_frame','nokey','-i',str(p),'-an','-sn','-dn','-vf','fps=1/3,scale=128:128:force_original_aspect_ratio=decrease,pad=128:128:(ow-iw)/2:(oh-ih)/2','-frames:v','8','-threads','1','-f','rawvideo','-pix_fmt','rgb24','pipe:1']
        pr=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=50,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        err=pr.stderr.decode('utf-8','replace'); dur=re.search(r'Duration: (\d+):(\d+):([\d.]+)',err)
        if dur: result['duration']=int(dur[1])*3600+int(dur[2])*60+float(dur[3])
        video=next((line for line in err.splitlines() if 'Video:' in line),'')
        if not video and re.search(r'Stream #.*Audio:',err):
            result.update(status='仅音频，扩展名为MP4',method='FFmpeg头信息',detail='实际检测到音频流，未检测到视频流。保留原文件与原扩展名，待人工确认。')
            return result
        dims=re.search(r'\b(\d{2,5})x(\d{2,5})\b',video)
        if dims: result.update(width=int(dims[1]),height=int(dims[2]))
        codec=re.search(r'Video: ([^ ,]+)',video)
        if codec: result['codec']=codec[1]
        size=128*128*3; frames=[Image.frombytes('RGB',(128,128),pr.stdout[i:i+size]) for i in range(0,len(pr.stdout)-size+1,size)]
        if not frames and result['duration']:
            skip=args.index('-skip_frame'); del args[skip:skip+2]
            if result['duration']<3: args[args.index('-vf')+1]='scale=128:128:force_original_aspect_ratio=decrease,pad=128:128:(ow-iw)/2:(oh-ih)/2'
            pr=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            err=pr.stderr.decode('utf-8','replace')
            result['method']='关键帧抽样无输出后补查普通帧；前24秒每3秒取样，最多8帧；非完整解码'
            frames=[Image.frombytes('RGB',(128,128),pr.stdout[i:i+size]) for i in range(0,len(pr.stdout)-size+1,size)]
        hashes=[]
        for im in frames:
            # Remove padding before computing hashes; common black bars must not dominate similarity.
            if result['width'] and result['height']:
                scale=min(128/result['width'],128/result['height'])
                cw,ch=round(result['width']*scale),round(result['height']*scale)
                crop=im.crop(((128-cw)//2,(128-ch)//2,(128-cw)//2+cw,(128-ch)//2+ch))
            else: crop=im
            gray=crop.convert('L')
            values=np.asarray(gray.resize((9,8))).ravel().tolist(); bits=0
            for y in range(8):
                for x in range(8): bits=(bits<<1)|int(values[y*9+x]>values[y*9+x+1])
            arr=np.asarray(gray.resize((32,32)),dtype=float)
            coeff=(DCT@arr@DCT.T).ravel(); median=float(np.median(coeff[1:])); ph=0
            for value in coeff: ph=(ph<<1)|int(value>median)
            # Uniform frames carry no useful matching evidence.
            hashes.append(format(bits,'016x')+format(ph,'016x') if arr.std()>6 else '0'*32)
        if frames:
            indexes=sorted(set([0,len(frames)//2,len(frames)-1])); strip=Image.new('RGB',(128*len(indexes),128))
            for i,j in enumerate(indexes): strip.paste(frames[j],(128*i,0))
            thumb=LIB/'报告'/'缩略图'/(r['id']+'.jpg'); strip.save(thumb,quality=82)
            result.update(thumbnail=str(thumb),hashes_json=json.dumps(hashes),status='抽样可读取' if pr.returncode==0 else '解码有告警')
        else: result.update(status='无法抽帧',detail=err[-1800:])
        errors=[s for s in err.splitlines() if any(t in s.lower() for t in ['invalid data','error','corrupt','truncat'])]
        if errors: result.update(status='解码有告警',detail='\n'.join(errors)[:2000])
    except Exception as e: result.update(status='检查失败',detail=str(e))
    return result

def media(c):
    rows=[dict(r) for r in c.execute('SELECT f.* FROM files f LEFT JOIN media m ON m.file_id=f.id WHERE m.file_id IS NULL')]
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        for i,r in enumerate(ex.map(media_one,rows),1):
            keys=list(r); c.execute(f"INSERT OR REPLACE INTO media({','.join(keys)}) VALUES({','.join('?' for _ in keys)})",list(r.values()))
            if i%50==0: c.commit(); print('媒体检查',i,'/',len(rows),flush=True)
    c.commit(); print('媒体检查完成',flush=True)

def plan(c):
    # Never overwrite an executed plan; rows can be resumed after interruption.
    groups=defaultdict(list)
    for r in c.execute('SELECT * FROM files ORDER BY original_path'): groups[(r['sha256'],r['extension'])].append(dict(r))
    for group in groups.values():
        keeper=next((r for r in group if r['duplicate_of'] is None and r['status']=='已归档'),group[0])
        for r in group:
            if r['status'] in ['已归档','已暂存重复']: continue
            dup=None if r['id']==keeper['id'] else keeper['id']
            if dup: dest=LIB/'虚拟回收站'/r['batch_id']/(safe(Path(r['original_path']).stem)+'__'+r['id']+r['extension'])
            else:
                creator=c.execute('SELECT * FROM creators WHERE id=?',(r['creator_id'],)).fetchone()
                if r['kind']=='other': folder=LIB/'其他资料'/r['batch_id']
                elif r['kind']=='incomplete': folder=LIB/'待确认'/'下载未完成'
                elif c.execute("SELECT 1 FROM media WHERE file_id=? AND status='仅音频，扩展名为MP4'",(r['id'],)).fetchone(): folder=LIB/'待确认'/'扩展名与内容不符'
                elif not creator: folder=LIB/'待确认'/'博主不明'
                else: folder=LIB/'博主'/(safe(creator['name'],35)+'__'+creator['id'])/('视频' if r['kind']=='video' else '图片及音频')
                work=c.execute('SELECT title FROM works WHERE id=?',(r['work_id'],)).fetchone()
                title=work['title'] if work else Path(r['original_path']).stem
                dest=folder/(safe(title)+'__'+r['id']+r['extension'])
            if len(str(dest))>245: raise RuntimeError('目标路径过长：'+str(dest))
            c.execute('UPDATE files SET duplicate_of=?,target_path=? WHERE id=?',(dup,str(dest),r['id']))
    c.commit()
    targets=[r[0].casefold() for r in c.execute('SELECT target_path FROM files')]
    if len(targets)!=len(set(targets)): raise RuntimeError('目标路径冲突')
    export_csv(c,'SELECT id,original_path,target_path,sha256,duplicate_of,status FROM files','搬动与回滚清单.csv',LIB/'操作记录')
    backup(c,'归档前.sqlite3'); print('整理计划已生成',flush=True)

def move_file(c,r,reverse=False):
    src=Path(r['current_path']); dst=Path(r['original_path'] if reverse else r['target_path'])
    if not any(inside(r['original_path'],root) for root in ALLOWED+[LIB/'待导入',LEGACY_ROOT]): raise RuntimeError('来源不在允许范围')
    if any(inside(src,root) or inside(dst,root) for root in EXCLUDED): raise RuntimeError('来源目录被排除')
    if not reverse and not inside(dst,LIB): raise RuntimeError('归档目标越界')
    if src==dst: return
    if not src.is_file() or dst.exists(): raise RuntimeError('来源缺失或目标冲突：'+str(src)+' -> '+str(dst))
    if digest(src)!=r['sha256']: raise RuntimeError('来源内容已改变：'+str(src))
    dst.parent.mkdir(parents=True,exist_ok=True)
    cur=c.execute('INSERT INTO moves(file_id,batch_id,src,dst,sha256,state,created_at) VALUES(?,?,?,?,?,?,?)',(r['id'],r['batch_id'],str(src),str(dst),r['sha256'],'准备恢复' if reverse else '准备搬动',NOW())); mid=cur.lastrowid; c.commit()
    src.rename(dst) # Same-volume rename, refuses existing destinations on Windows.
    if digest(dst)!=r['sha256']: raise RuntimeError('搬动后校验失败：'+str(dst))
    status='已恢复' if reverse else '已暂存重复' if r['duplicate_of'] else '已归档'
    c.execute('UPDATE files SET current_path=?,status=? WHERE id=?',(str(dst),status,r['id']))
    c.execute('UPDATE moves SET state=?,completed_at=? WHERE id=?',('已恢复' if reverse else '已完成',NOW(),mid)); c.commit()
    with (LIB/'操作记录'/'操作日志.jsonl').open('a',encoding='utf-8') as f:
        f.write(json.dumps(dict(move_id=mid,file_id=r['id'],src=str(src),dst=str(dst),sha256=r['sha256'],action=status,time=NOW()),ensure_ascii=False)+'\n'); f.flush(); os.fsync(f.fileno())

def recover_pending(c):
    for m in c.execute("SELECT * FROM moves WHERE state IN ('准备恢复','准备搬动') ORDER BY id").fetchall():
        src,dst=Path(m['src']),Path(m['dst'])
        if src.is_file() and not dst.exists():
            if digest(src)!=m['sha256']: raise RuntimeError('待处理来源校验不符')
            c.execute("UPDATE moves SET state='未执行' WHERE id=?",(m['id'],))
        elif dst.is_file() and not src.exists() and digest(dst)==m['sha256']:
            row=c.execute('SELECT * FROM files WHERE id=?',(m['file_id'],)).fetchone()
            status='已恢复' if m['state']=='准备恢复' else '已暂存重复' if row['duplicate_of'] else '已归档'
            c.execute('UPDATE files SET current_path=?,status=? WHERE id=?',(str(dst),status,row['id']))
            c.execute("UPDATE moves SET state='已恢复中断',completed_at=? WHERE id=?",(NOW(),m['id']))
        else: raise RuntimeError('中断记录存在路径冲突，停止处理：'+str(m['id']))
    c.commit()

def apply(c,limit=0):
    recover_pending(c)
    rows=c.execute("SELECT * FROM files WHERE current_path!=target_path ORDER BY duplicate_of IS NOT NULL,id").fetchall()
    if limit: rows=rows[:limit]
    for i,r in enumerate(rows,1):
        move_file(c,r)
        if i%100==0: print('归档',i,'/',len(rows),flush=True)
    print('本次归档',len(rows),flush=True)

def rollback(c,ids=None,batch=None):
    recover_pending(c)
    rows=c.execute('SELECT * FROM files WHERE current_path!=original_path').fetchall()
    if ids: rows=[r for r in rows if r['id'] in ids]
    if batch: rows=[r for r in rows if r['batch_id']==batch]
    destinations=[r['original_path'].casefold() for r in rows]
    if len(destinations)!=len(set(destinations)): raise RuntimeError('不同导入批次曾使用同一原路径，请按批次恢复以避免覆盖')
    # Preflight all conflicts before restoring anything.
    for r in rows:
        if Path(r['original_path']).exists(): raise RuntimeError('恢复位置已存在文件：'+r['original_path'])
    for r in rows: move_file(c,r,True)
    print('恢复',len(rows),'个文件',flush=True)

def similarities(c):
    rows=[dict(r) for r in c.execute("SELECT f.*,w.platform_id,m.duration,m.width,m.height,m.hashes_json FROM files f JOIN media m ON m.file_id=f.id JOIN works w ON w.id=f.work_id WHERE f.kind='video' AND f.duplicate_of IS NULL AND m.duration IS NOT NULL")]
    direct=defaultdict(list)
    for r in rows: direct[r['platform_id'] or r['work_id']].append(r)
    for group in direct.values():
        for i,a in enumerate(group):
            for b in group[i+1:]:
                x,y=sorted([a['id'],b['id']])
                c.execute('INSERT OR IGNORE INTO similar(a,b,reason,score) VALUES(?,?,?,?)',(x,y,'作品编号或本地作品关联相同，但文件内容不同；需核对版本和作者',1.0))
    rows.sort(key=lambda x:x['duration']); count=0
    for i,a in enumerate(rows):
        ah=[int(h,16) for h in json.loads(a['hashes_json'])]
        if not ah: continue
        for b in rows[i+1:]:
            if b['duration']-a['duration']>max(1.5,a['duration']*.05): break
            bh=[int(h,16) for h in json.loads(b['hashes_json'])]
            if not bh: continue
            # Two aligned low-distance samples, not a single common opening frame.
            ds=[(x^y).bit_count() for x,y in zip(ah,bh)]
            good=sum(x!=0 and y!=0 and ((x^y)>>64).bit_count()<=8 and ((x^y)&((1<<64)-1)).bit_count()<=10 for x,y in zip(ah,bh))
            same_id=a['work_id']==b['work_id'] or bool(a['platform_id'] and a['platform_id']==b['platform_id'])
            distinct=min(len(set(ah)-{0}),len(set(bh)-{0}))
            if same_id or (distinct>=2 and len(ds)>=2 and good>=max(2,math.ceil(len(ds)*.6)) and sum(ds)/len(ds)<=18):
                x,y=sorted([a['id'],b['id']]); score=round(1-sum(ds)/len(ds)/128,4)
                reason='相同作品ID或已关联作品，不同文件内容' if same_id else f'抽样画面相似；{good}/{len(ds)}帧同时通过dHash≤8位与pHash≤10位；时长差{abs(a["duration"]-b["duration"]):.2f}秒'
                c.execute('INSERT OR IGNORE INTO similar(a,b,reason,score) VALUES(?,?,?,?)',(x,y,reason,score)); count+=1
    c.commit(); print('相似候选',count,flush=True)

def export_csv(c,query,name,folder=None):
    path=(folder or LIB/'报告')/name; cur=c.execute(query); names=[x[0] for x in cur.description]
    labels={'id':'记录编号','file_id':'文件编号','work_id':'作品编号','creator_id':'博主编号','batch_id':'导入批次','creator':'博主名','name':'博主名','title':'标题','platform_id':'平台标识','url':'作品链接','current_path':'现路径','original_path':'原路径','target_path':'计划路径','sha256':'SHA256校验值','size':'大小_字节','mtime_ns':'原文件修改时间_纳秒','extension':'扩展名','kind':'文件类别','duplicate_of':'保留副本编号','status':'状态','author_basis':'作者判定依据','association_status':'作品关联状态','backup_status':'独立备份状态','duration':'时长_秒','width':'宽度_像素','height':'高度_像素','media_status':'媒体读取状态','retained_path':'保留副本路径','identity_status':'身份确认状态','aliases_json':'历史昵称_JSON','a':'文件A编号','b':'文件B编号','path_a':'文件A路径','path_b':'文件B路径','reason':'原因或匹配依据','score':'相似参考值_非概率','decision':'人工判断','note':'备注','detail':'详细记录','metric':'指标','value':'数值','observed_at':'观察时间','recorded_at':'入库时间','source':'来源','source_file':'来源文件','source_locator':'来源位置','value_kind':'数值类型','confidence':'可信依据','evidence':'证据','attempted_at':'查询时间','http_status':'HTTP状态'}
    labels.update(filename_platform_id='文件名作品编号',retained_id='保留副本编号',retained_original_path='保留副本原路径',retained_platform_id='保留副本作品编号',retained_creator='保留副本博主编号')
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        out=csv.writer(f); out.writerow([labels.get(n,n) for n in names])
        for r in cur:
            # Prevent spreadsheet formula execution when viewing externally sourced titles.
            out.writerow([("'"+v if isinstance(v,str) and (v.startswith(('=','+','-','@')) or (v.isdigit() and len(v)>15)) else v) for v in r])

def verify(c):
    recover_pending(c); rows=c.execute("SELECT * FROM files WHERE status!='已永久删除'").fetchall(); problems=[]
    def check(r):
        p=Path(r['current_path'])
        if not p.is_file(): return dict(id=r['id'],issue='文件缺失',path=str(p))
        if p.stat().st_size!=r['size'] or digest(p)!=r['sha256']: return dict(id=r['id'],issue='内容校验不符',path=str(p))
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        for i,r in enumerate(ex.map(check,rows),1):
            if r: problems.append(r)
            if i%1000==0: print('内容复核',i,'/',len(rows),flush=True)
    indexed={r['current_path'] for r in rows}
    checked_roots=ALLOWED+[LEGACY_ROOT,LIB/'待导入']
    leftovers=[str(p) for root in checked_roots if root.exists() for p in root.rglob('*') if p.is_file() and str(p) not in indexed]
    integrity=c.execute('PRAGMA integrity_check').fetchone()[0]
    fk=list(c.execute('PRAGMA foreign_key_check'))
    data=dict(checked_at=NOW(),files=len(rows),bytes=sum(r['size'] for r in rows),content_failures=problems,unindexed_source_files=leftovers,sqlite_integrity=integrity,foreign_key_failures=[list(x) for x in fk],scope=[str(x) for x in checked_roots],excluded=[str(x) for x in EXCLUDED],health_scope='抽样解码，不保证每一帧均无损坏',backup_status='仅索引有本地备份；未验证独立硬盘视频备份')
    write_json(LIB/'报告'/'验收核对.json',data)
    if problems or leftovers or fk or integrity!='ok': raise RuntimeError('验收未通过，见验收核对.json')
    backup(c,'验收后.sqlite3'); print('文件内容与索引核对通过',len(rows),flush=True)

def reconcile_filename_ids(c):
    # Identical downloaded bytes do not prove that differing platform work IDs are the same work.
    for r in c.execute("SELECT f.*,w.platform_id indexed_platform FROM files f JOIN works w ON w.id=f.work_id WHERE f.kind='video'").fetchall():
        match=re.search(r'(?:^|_)(\d{18,20})$',Path(r['original_path']).stem)
        if not match: continue
        pid=match.group(1)
        c.execute('UPDATE files SET filename_platform_id=? WHERE id=?',(pid,r['id']))
        if r['indexed_platform']==pid: continue
        old=c.execute('SELECT id FROM works WHERE platform_id=? AND creator_id IS ?',(pid,r['creator_id'])).fetchone()
        wid=old['id'] if old else 'W'+uuid.uuid4().hex[:16]
        title=re.sub(r'_?'+pid+'$','',Path(r['original_path']).stem)
        if title in ['douyin_video','douyin','video']:title='标题未知'
        c.execute('INSERT OR IGNORE INTO works VALUES(?,?,?,?,?,NULL,?,?)',(wid,r['creator_id'],title,pid,'https://www.douyin.com/video/'+pid,json.dumps(re.findall(r'#([^#]+)',title),ensure_ascii=False),'作品ID来自文件名'))
        c.execute('UPDATE files SET work_id=? WHERE id=?',(wid,r['id']))
        c.execute('INSERT INTO review_log(created_at,object_type,object_id,old_json,new_json) VALUES(?,?,?,?,?)',(NOW(),'filename_identity_reconciliation',r['id'],json.dumps({'work_id':r['work_id'],'platform_id':r['indexed_platform']}),json.dumps({'work_id':wid,'platform_id':pid})))
    c.execute("UPDATE works SET title='标题未知' WHERE title IN ('douyin_video','douyin','video')")
    c.commit()

def enrich(c,limit=5):
    rows=c.execute("SELECT * FROM works WHERE platform_id IS NOT NULL AND id NOT IN (SELECT work_id FROM enrichment) ORDER BY id LIMIT ?",(limit,)).fetchall()
    gates=0
    for w in rows:
        url=w['url']; status='无可核验统计'; detail=''; code=None; data=b''
        try:
            req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Accept-Language':'zh-CN,zh;q=0.9'})
            with urllib.request.urlopen(req,timeout=12) as resp: data=resp.read(3_000_000); code=resp.status
            text=data.decode('utf-8','replace'); payloads=[]
            for tag in re.findall(r'<script[^>]*>(.*?)</script>',text,re.S):
                try: payloads.append(json.loads(urllib.parse.unquote(html.unescape(tag))))
                except (ValueError,TypeError): pass
            def walk(obj):
                if isinstance(obj,dict):
                    if str(obj.get('aweme_id',obj.get('awemeId','')))==w['platform_id']:
                        stat=obj.get('statistics',obj.get('stats',{}))
                        if isinstance(stat,dict) and stat: yield stat
                    for value in obj.values(): yield from walk(value)
                elif isinstance(obj,list):
                    for value in obj: yield from walk(value)
            stats=[s for payload in payloads for s in walk(payload)]
            found=0
            for s in stats[:1]:
                for key,metric in [('play_count','播放量'),('collect_count','收藏量'),('digg_count','点赞量'),('comment_count','评论量'),('share_count','分享量')]:
                    value=s.get(key)
                    if isinstance(value,(int,float)) and value>=0:
                        # Public play_count=0 commonly means unavailable; do not treat it as observed zero.
                        if key=='play_count' and value==0: continue
                        c.execute('INSERT INTO metrics(work_id,metric,value,observed_at,recorded_at,source,value_kind,confidence,evidence) VALUES(?,?,?,?,?,?,?,?,?)',(w['id'],metric,value,NOW(),NOW(),url,'公开页面当前记录','作品ID精确匹配',json.dumps(s,ensure_ascii=False))); found+=1
            if found: status='补充成功'
            elif any(t in text.lower() for t in ['captcha','验证码','verifycenter','校验','douyin']): status='公开页面未提供可核验统计或需验证'
            detail=f'HTTP {code}；响应{len(data)}字节；按作品ID解析到{found}项。未使用登录信息。'
        except Exception as e: status='访问失败'; detail=str(e)
        c.execute('INSERT INTO enrichment(work_id,attempted_at,url,status,http_status,detail) VALUES(?,?,?,?,?,?)',(w['id'],NOW(),url,status,code,detail)); c.commit()
        print('公开资料查询',w['platform_id'],status,flush=True)
        gates=gates+1 if status!='补充成功' else 0
        if gates>=3:
            print('连续3次无有效统计，停止批量访问；剩余项保留待补充队列',flush=True); break
        time.sleep(.5)

def import_legacy_tables(c):
    try: import openpyxl
    except ImportError: raise RuntimeError('缺少 openpyxl，无法读取旧下载器表格')
    root=SOURCE/'快斗下载器'/'快抖下载器'; tables=list(root.rglob('*.xlsx')); imported=0; linked=0
    for xlsx in tables:
        wb=openpyxl.load_workbook(xlsx,read_only=True,data_only=True)
        ws=wb.active; it=ws.values
        try: headers=list(next(it))
        except StopIteration: wb.close(); continue
        for row_no,vals in enumerate(it,2):
            d=dict(zip(headers,vals)); pid=str(d.get('作品ID') or '').strip() or None
            author=str(d.get('作者昵称') or '').strip() or None
            if not pid: continue
            title=str(d.get('标题') or '').strip() or '标题未知'
            cid=None
            if author:
                cid='C'+hashlib.sha256(('legacy:'+author).encode()).hexdigest()[:12]
                c.execute('INSERT OR IGNORE INTO creators VALUES(?,?,NULL,?,?)',(cid,author,'表格作者，账号待核实',json.dumps([author],ensure_ascii=False)))
            wid=c.execute('SELECT id FROM works WHERE platform_id=? AND (creator_id IS ? OR creator_id=?) LIMIT 1',(pid,cid,cid)).fetchone()
            if wid: wid=wid['id']
            else:
                wid='W'+uuid.uuid4().hex[:16]
                c.execute('INSERT INTO works VALUES(?,?,?,?,?,NULL,?,?)',(wid,cid,title,pid,d.get('作品链接') or 'https://www.douyin.com/video/'+pid,json.dumps(str(d.get('标签') or '').split(),ensure_ascii=False),'表格作品ID'))
            for field,metric in [('点赞量','点赞量'),('评论量','评论量'),('收藏量','收藏量'),('分享量','分享量'),('粉丝量','粉丝量')]:
                val=d.get(field)
                if isinstance(val,(int,float)) and val>=0:
                    c.execute('INSERT INTO metrics(work_id,metric,value,observed_at,recorded_at,source,source_file,source_locator,value_kind,confidence,evidence) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(wid,metric,val,d.get('发布时间'),NOW(),'快抖下载器Excel',str(xlsx),f'工作表:{ws.title} 行:{row_no}','历史下载时快照','表格原始记录',json.dumps({'作品ID':pid,'作者昵称':author},ensure_ascii=False)))
            # Match the actual local file by sequence prefix in this author's directory.
            seq=d.get('序号'); matches=[]
            if seq is not None:
                for p in xlsx.parent.glob(f'{int(seq)}_*.mp4' if str(seq).isdigit() else '*.mp4'): matches.append(p)
                if not matches and str(seq).isdigit(): matches=list(xlsx.parent.glob(f'*_{int(seq)}_*.mp4'))
            if not matches:
                save=str(d.get('保存位置') or '')
                stem=Path(save).stem if save else ''
                if stem:
                    matches=[p for p in xlsx.parent.glob('*.mp4') if p.stem==stem]
            for p in matches:
                fr=c.execute('SELECT id FROM files WHERE original_path=? OR current_path=?',(str(p),str(p))).fetchone()
                if fr:
                    c.execute('UPDATE files SET work_id=?,creator_id=?,author_basis=?,association_status=? WHERE id=?',(wid,cid,'Excel作者与作品ID','表格行匹配',fr['id'])); linked+=1
            imported+=1
        wb.close()
    c.commit(); write_json(LIB/'操作记录'/'旧快抖表格导入.json',dict(imported_rows=imported,linked_files=linked,table_count=len(tables),root=str(root),imported_at=NOW()))
    print('旧快抖表格导入',imported,'行，关联文件',linked,flush=True)

def report(c):
    export_csv(c,'SELECT id,batch_id,original_path,current_path,target_path,sha256,duplicate_of,status FROM files','搬动与回滚清单.csv',LIB/'操作记录')
    export_csv(c,'SELECT * FROM video_index','视频索引.csv')
    export_csv(c,'SELECT * FROM files','全部文件清单.csv')
    export_csv(c,'SELECT * FROM creators','博主清单.csv')
    export_csv(c,"SELECT f.id,f.original_path,f.current_path,f.duplicate_of,k.current_path retained_path,f.sha256,f.size FROM files f JOIN files k ON f.duplicate_of=k.id",'完全重复清单.csv')
    export_csv(c,"SELECT f.id,f.original_path,f.filename_platform_id,k.id retained_id,k.original_path retained_original_path,k.filename_platform_id retained_platform_id,f.creator_id,k.creator_id retained_creator FROM files f JOIN files k ON f.duplicate_of=k.id WHERE f.kind='video' AND (f.filename_platform_id IS NOT k.filename_platform_id OR f.creator_id IS NOT k.creator_id)",'数据冲突清单.csv')
    export_csv(c,'SELECT s.*,a.current_path path_a,b.current_path path_b FROM similar s JOIN files a ON s.a=a.id JOIN files b ON s.b=b.id','相似视频清单.csv')
    export_csv(c,"SELECT f.id,f.current_path,f.original_path,f.author_basis,f.association_status,m.status,m.detail FROM files f LEFT JOIN media m ON m.file_id=f.id WHERE f.creator_id IS NULL OR (f.kind='image' AND f.work_id IS NULL) OR m.status IN ('下载未完成','图片异常','无法抽帧','检查失败','解码有告警','仅音频，扩展名为MP4')",'待确认与异常.csv')
    export_csv(c,'SELECT * FROM metrics','热度记录.csv')
    export_csv(c,'SELECT * FROM enrichment','联网查询记录.csv')
    export_csv(c,"SELECT w.id,w.title,w.platform_id,w.url,CASE WHEN w.platform_id IS NULL THEN '缺少可靠作品ID' WHEN e.work_id IS NULL THEN '待查询：公开访问试查未获有效统计，暂缓批量查询' ELSE e.status END reason FROM works w LEFT JOIN enrichment e ON e.work_id=w.id WHERE NOT EXISTS(SELECT 1 FROM metrics m WHERE m.work_id=w.id AND m.metric IN ('播放量','收藏量'))",'热度待补充.csv')
    rows=[dict(r) for r in c.execute('SELECT * FROM files')]
    stat=dict(generated_at=NOW(),file_count=len(rows),video_files=sum(r['kind']=='video' for r in rows),bytes=sum(r['size'] for r in rows),creators=c.execute('SELECT count(*) FROM creators').fetchone()[0],works=c.execute('SELECT count(*) FROM works').fetchone()[0],duplicate_files=sum(bool(r['duplicate_of']) for r in rows),similar_pairs=c.execute('SELECT count(*) FROM similar').fetchone()[0],statuses=dict(Counter(r['status'] for r in rows)),health=dict(c.execute('SELECT status,count(*) FROM media GROUP BY status').fetchall()),metrics=c.execute('SELECT count(*) FROM metrics').fetchone()[0],excluded=[str(x) for x in EXCLUDED])
    write_json(LIB/'报告'/'整理汇总.json',stat)
    esc=lambda x:html.escape(str(x or ''))
    def link(path,label): return '<a href="'+esc(Path(path).as_uri())+'">'+esc(label)+'</a>'
    def card(fid):
        r=c.execute('SELECT f.*,w.title,c.name,m.duration,m.width,m.height,m.thumbnail FROM files f LEFT JOIN works w ON f.work_id=w.id LEFT JOIN creators c ON f.creator_id=c.id LEFT JOIN media m ON m.file_id=f.id WHERE f.id=?',(fid,)).fetchone()
        thumb=('<img loading="lazy" src="缩略图/'+fid+'.jpg" alt="视频抽样画面">') if r['thumbnail'] else '<p>暂无缩略图</p>'
        return '<article>'+thumb+'<h3>'+esc(r['name'] or '博主待确认')+' · '+esc(r['title'])+'</h3><p>'+esc(r['duration'])+' 秒 / '+esc(r['width'])+'×'+esc(r['height'])+' / '+f'{r["size"]/1048576:.2f}'+' MB</p><p>'+link(r['current_path'],'打开视频')+'　'+link(str(Path(r['current_path']).parent),'打开所在文件夹')+'</p><small>'+esc(r['id'])+'<br>'+esc(r['current_path'])+'</small></article>'
    pairs=[dict(r) for r in c.execute('SELECT * FROM similar ORDER BY score DESC')]
    data_json=json.dumps(pairs,ensure_ascii=False).replace('<','\\u003c')
    header='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>相似视频对照</title><style>body{font:16px/1.6 "Microsoft YaHei",sans-serif;margin:30px auto;max-width:1150px;padding:0 20px;background:#f5f6fa;color:#202535}h1{font-size:28px}.pair{background:white;padding:20px;margin:20px 0;border:1px solid #dce1eb;border-radius:12px}.cards{display:grid;grid-template-columns:1fr 1fr;gap:20px}article{min-width:0}img{width:100%;max-width:384px;height:128px;object-fit:contain;background:#111}small{overflow-wrap:anywhere;color:#555}button,select,input{padding:8px;margin:5px}a{color:#224bad}h3{font-size:16px}@media(max-width:700px){.cards{grid-template-columns:1fr}}</style><h1>相似视频对照</h1>'''
    body='<p>这里只列候选，不自动合并或删除。抽帧主要覆盖前24秒，裁切、变速、镜像和长视频后段可能漏检。播放器和文件夹链接使用本地文件。</p><p>可逐组记录判断，再点击“下载复核结果”保存；导入后写入正式索引。浏览器临时保存不能替代导出的文件。</p><button onclick="download()">下载复核结果</button><input id="filter" placeholder="搜索博主、标题或编号" oninput="filterPairs()">'
    for p in pairs:
        key=p['a']+'_'+p['b']; body+='<section class="pair" data-key="'+key+'"><p>'+esc(p['reason'])+'；相似参考值 '+esc(p['score'])+'；当前判断：'+esc(p['decision'])+'</p><div class="cards">'+card(p['a'])+card(p['b'])+'</div><label>你的判断 <select data-review="'+key+'"><option>待确认</option><option>同一作品的不同版本</option><option>不同作品</option><option>暂不确定</option></select></label><input data-note="'+key+'" placeholder="备注"></section>'
    if not pairs: body+='<p>本轮未检出相似候选。</p>'
    js='''<script>const pairs=DATA;const prefix='douyin-review-v1:';document.querySelectorAll('[data-review]').forEach(s=>{let key=s.dataset.review;let old;try{old=JSON.parse(localStorage.getItem(prefix+key)||'null')}catch(e){}let p=pairs.find(p=>p.a+'_'+p.b===key);s.value=old?.decision||p?.decision||'待确认';let n=document.querySelector('[data-note="'+key+'"]');n.value=old?.note||p?.note||'';function save(){try{localStorage.setItem(prefix+key,JSON.stringify({decision:s.value,note:n.value}))}catch(e){}}s.onchange=save;n.oninput=save});function download(){const reviews=[...document.querySelectorAll('[data-review]')].map(s=>{let p=pairs.find(p=>p.a+'_'+p.b===s.dataset.review);return {a:p.a,b:p.b,decision:s.value,note:document.querySelector('[data-note="'+s.dataset.review+'"]').value}});let a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify({schema:1,reviews},null,2)],{type:'application/json'}));a.download='相似视频复核结果.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)}function filterPairs(){let q=document.getElementById('filter').value.toLowerCase();document.querySelectorAll('.pair').forEach(p=>p.hidden=!p.textContent.toLowerCase().includes(q))}</script>'''.replace('DATA',data_json)
    (LIB/'报告'/'相似视频对照.html').write_text(header+body+js+'</html>',encoding='utf-8')
    issues=c.execute("SELECT f.id,f.creator_id,f.association_status,m.status,m.detail FROM files f LEFT JOIN media m ON m.file_id=f.id WHERE f.status!='已永久删除' AND (f.creator_id IS NULL OR (f.kind='image' AND f.work_id IS NULL) OR m.status IN ('下载未完成','图片异常','无法抽帧','检查失败','解码有告警','仅音频，扩展名为MP4')) ORDER BY m.status,f.id").fetchall()
    issue_body='<p>所有文件均保留。这里汇总作者不明、图片尚未关联、类型不符、未完成下载和抽样解码告警。文件内容未作修复或转码。</p>'
    for r in issues:
        label=('博主不明；' if not r['creator_id'] else '')+str(r['status'])+'；'+str(r['association_status'])
        issue_body+='<section class="pair"><h2>'+esc(label)+'</h2>'+card(r['id'])+'<p>'+esc(r['detail'])+'</p></section>'
    (LIB/'报告'/'待确认与异常对照.html').write_text(header.replace('相似视频对照','待确认与异常对照')+issue_body+'</html>',encoding='utf-8')
    conflicts=c.execute("SELECT f.id,k.id retained_id,f.filename_platform_id,k.filename_platform_id retained_platform_id,f.creator_id,k.creator_id retained_creator FROM files f JOIN files k ON f.duplicate_of=k.id WHERE f.status!='已永久删除' AND k.status!='已永久删除' AND f.kind='video' AND (f.filename_platform_id IS NOT k.filename_platform_id OR f.creator_id IS NOT k.creator_id)").fetchall()
    conflict_body='<p>下面的文件字节完全相同，但来源目录的作者或文件名中的作品编号不同。可能是转发、错放或下载错误。副本仅暂存，来源与不同作品身份均保留，没有自动合并账号。</p>'
    for r in conflicts:
        conflict_body+='<section class="pair"><p>文件名作品编号：'+esc(r['filename_platform_id'])+' / '+esc(r['retained_platform_id'])+'；作者分类：'+esc(r['creator_id'])+' / '+esc(r['retained_creator'])+'</p><div class="cards">'+card(r['id'])+card(r['retained_id'])+'</div></section>'
    (LIB/'报告'/'数据冲突对照.html').write_text(header.replace('相似视频对照','数据冲突对照')+conflict_body+'</html>',encoding='utf-8')
    overview='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>本地视频库整理报告</title><style>body{font:17px/1.8 "Microsoft YaHei",sans-serif;max-width:900px;margin:40px auto;padding:0 20px}table{border-collapse:collapse;width:100%}td,th{padding:10px;border-bottom:1px solid #ddd;text-align:left}a{color:#224bad}</style><h1>本地视频库整理报告</h1><p>本库包含两个抖音下载目录及旧快抖下载器目录；旧快抖原表格已保留并登记历史指标来源。</p><table>'
    for label,key in [('全部文件','file_count'),('MP4扩展名文件（含待确认纯音频）','video_files'),('暂定博主身份','creators'),('作品记录','works'),('完全重复副本','duplicate_files'),('相似候选对','similar_pairs'),('已补充热度记录','metrics')]: overview+='<tr><th>'+label+'</th><td>'+str(stat[key])+'</td></tr>'
    overview+='</table><p>博主来自目录名，尚未核实平台账号；不同来源的同名目录暂不合并。播放量与收藏量缺失保持未知。本轮无证据支持估算。</p><p>媒体检查为抽样解码，完整性校验为SHA-256；未验证独立硬盘备份。</p><h2>文件检查</h2><table>'
    for label,count in stat['health'].items(): overview+='<tr><th>'+esc(label)+'</th><td>'+str(count)+'</td></tr>'
    overview+='</table><h2>查看清单</h2><ul>'
    for name in ['相似视频对照.html','数据冲突对照.html','待确认与异常对照.html','视频索引.csv','全部文件清单.csv','博主清单.csv','完全重复清单.csv','待确认与异常.csv','热度待补充.csv','联网查询记录.csv','验收核对.json']:
        overview+='<li><a href="'+name+'">'+name+'</a></li>'
    overview+='</ul><h2>后续步骤</h2><p>复核博主身份、相似候选及未关联图片；新增下载放入待导入区。下一阶段播放器读取索引，随后采集个人观看反馈。</p></html>'
    (LIB/'报告'/'开始查看.html').write_text(overview,encoding='utf-8'); backup(c,'最新索引.sqlite3')
    print(json.dumps(stat,ensure_ascii=False,indent=2),flush=True)

def import_reviews(c,path):
    obj=json.loads(Path(path).read_text(encoding='utf-8-sig'))
    allowed={'待确认','同一作品的不同版本','不同作品','暂不确定'}
    with c:
        for r in obj['reviews']:
            if r['decision'] not in allowed: raise ValueError('未知判断')
            old=c.execute('SELECT * FROM similar WHERE a=? AND b=?',(r['a'],r['b'])).fetchone()
            if not old: raise ValueError('候选对不存在')
            c.execute('INSERT INTO review_log(created_at,object_type,object_id,old_json,new_json) VALUES(?,?,?,?,?)',(NOW(),'similar',r['a']+'_'+r['b'],json.dumps(dict(old),ensure_ascii=False),json.dumps(r,ensure_ascii=False)))
            c.execute('UPDATE similar SET decision=?,note=? WHERE a=? AND b=?',(r['decision'],str(r.get('note','')),r['a'],r['b']))
    print('复核结果已导入；不会自动删除或合并文件')

def correct_creator(c,args):
    old=c.execute('SELECT * FROM creators WHERE id=?',(args.creator,)).fetchone()
    if not old: raise ValueError('找不到博主编号')
    if not args.name and not args.platform_id and not args.merge_into: raise ValueError('需要名称、平台账号标识或明确的合并目标')
    with c:
        if args.merge_into:
            target=c.execute('SELECT * FROM creators WHERE id=?',(args.merge_into,)).fetchone()
            if not target or target['id']==old['id']: raise ValueError('合并目标无效')
            aliases=sorted(set(json.loads(target['aliases_json'])+json.loads(old['aliases_json'])))
            c.execute('UPDATE creators SET aliases_json=? WHERE id=?',(json.dumps(aliases,ensure_ascii=False),target['id']))
            c.execute("UPDATE files SET creator_id=?,author_basis='人工确认合并',status='待重新归类' WHERE creator_id=?",(target['id'],old['id']))
            c.execute('UPDATE works SET creator_id=? WHERE creator_id=?',(target['id'],old['id']))
            c.execute("UPDATE creators SET identity_status=? WHERE id=?",('已人工合并到'+target['id'],old['id']))
        else:
            name=args.name or old['name']; aliases=sorted(set(json.loads(old['aliases_json'])+[name]))
            c.execute("UPDATE creators SET name=?,platform_id=?,aliases_json=?,identity_status='人工确认' WHERE id=?",(name,args.platform_id or old['platform_id'],json.dumps(aliases,ensure_ascii=False),old['id']))
            c.execute("UPDATE files SET status='待重新归类',author_basis='人工确认' WHERE creator_id=?",(old['id'],))
        c.execute('INSERT INTO review_log(created_at,object_type,object_id,old_json,new_json) VALUES(?,?,?,?,?)',(NOW(),'creator',old['id'],json.dumps(dict(old),ensure_ascii=False),json.dumps(vars(args),ensure_ascii=False)))
    print('博主修正已记录；执行plan和apply后更新实际目录，原路径回滚信息仍保留')

def assign_author(c,args):
    if not args.ids: raise ValueError('需要文件编号')
    if not args.creator and not args.name: raise ValueError('需要现有博主编号或新博主名称')
    rows=[]
    for fid in args.ids:
        r=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
        if not r: raise ValueError('文件编号不存在：'+fid)
        rows.append(r)
    with c:
        if args.creator:
            cid=args.creator
            if not c.execute('SELECT 1 FROM creators WHERE id=?',(cid,)).fetchone(): raise ValueError('博主编号不存在')
        else:
            cid='C'+uuid.uuid4().hex[:12]
            c.execute('INSERT INTO creators VALUES(?,?,?,?,?)',(cid,args.name,args.platform_id,'人工确认',json.dumps([args.name],ensure_ascii=False)))
        for r in rows:
            linked=c.execute('SELECT * FROM files WHERE work_id=?',(r['work_id'],)).fetchall() if r['work_id'] else [r]
            if r['work_id']: c.execute("UPDATE works SET creator_id=?,identity_status='人工确认作者' WHERE id=?",(cid,r['work_id']))
            for f in linked:
                c.execute("UPDATE files SET creator_id=?,author_basis='人工确认',status='待重新归类' WHERE id=?",(cid,f['id']))
                c.execute('INSERT INTO review_log(created_at,object_type,object_id,old_json,new_json) VALUES(?,?,?,?,?)',(NOW(),'file_author',f['id'],json.dumps(dict(f),ensure_ascii=False),json.dumps({'creator_id':cid},ensure_ascii=False)))
    print('作者已修正',cid,'；同一作品关联文件一并修正，需再执行plan/apply更新目录')

def main():
    ap=argparse.ArgumentParser(description='离线视频库整理工具'); ap.add_argument('command',choices=['scan','legacy-import','media','plan','apply','rollback','similar','enrich','report','verify','import-reviews','correct-creator','assign-author']); ap.add_argument('--limit',type=int,default=0); ap.add_argument('--ids',nargs='*'); ap.add_argument('--batch'); ap.add_argument('--input'); ap.add_argument('--creator'); ap.add_argument('--name'); ap.add_argument('--platform-id'); ap.add_argument('--merge-into'); args=ap.parse_args()
    c=connect()
    try:
        if args.command=='scan': scan(c)
        elif args.command=='legacy-import': scan(c,True); import_legacy_tables(c)
        elif args.command=='media': media(c)
        elif args.command=='plan': plan(c)
        elif args.command=='apply': apply(c,args.limit)
        elif args.command=='rollback': rollback(c,args.ids,args.batch)
        elif args.command=='similar': similarities(c)
        elif args.command=='enrich': enrich(c,args.limit or 5)
        elif args.command=='report': report(c)
        elif args.command=='verify': verify(c)
        elif args.command=='import-reviews': import_reviews(c,args.input)
        elif args.command=='correct-creator': correct_creator(c,args)
        elif args.command=='assign-author': assign_author(c,args)
    finally: c.close()

if __name__=='__main__': main()
