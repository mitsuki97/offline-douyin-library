from __future__ import annotations
import argparse, hashlib, json, os, re, shutil, sqlite3, uuid
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
LIB = BASE / '本地视频库'
DB = LIB / '索引' / 'library.sqlite3'
INBOX = LIB / '待整理'
VIDEO_EXT = {'.mp4','.mov','.mkv','.webm','.avi','.m4v','.flv','.ts'}

def now(): return datetime.now().astimezone().isoformat(timespec='seconds')
def sha256(path, chunk=1024*1024):
    h=hashlib.sha256()
    with path.open('rb') as f:
        while b:=f.read(chunk): h.update(b)
    return h.hexdigest()
def safe_name(s): return re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',str(s)).strip()[:80] or '未命名来源'
def video_id(name):
    m=re.search(r'(?<!\d)(\d{15,25})(?!\d)',name)
    return m.group(1) if m else ''
def files_under(root): return [p for p in root.rglob('*') if p.is_file() and p.suffix.lower() in VIDEO_EXT]
def manifest_path(batch): return LIB/'索引'/'来源导入'/f'{batch}.json'
def write_json_atomic(path,obj):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')
    tmp.replace(path)

def scan(source):
    source=Path(source).resolve()
    if not source.is_dir(): raise SystemExit(f'来源目录不存在：{source}')
    batch='source_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]
    conn=sqlite3.connect(DB); conn.row_factory=sqlite3.Row
    known={r['sha256']:(r['id'],r['current_path']) for r in conn.execute("select id,sha256,current_path from files where sha256 is not null and sha256!=''")}
    by_pid={r['filename_platform_id']:(r['id'],r['current_path']) for r in conn.execute("select id,current_path,filename_platform_id from files where filename_platform_id is not null and filename_platform_id!=''")}
    items=[]
    for p in files_under(source):
        st=p.stat(); h=sha256(p); pid=video_id(p.stem); duplicate=known.get(h); pid_match=by_pid.get(pid) if pid else None
        items.append({'source_path':str(p),'relative_path':str(p.relative_to(source)),'name':p.name,'size':st.st_size,'mtime_ns':st.st_mtime_ns,'sha256':h,'filename_platform_id':pid,'duplicate_of':duplicate[0] if duplicate else '','duplicate_path':duplicate[1] if duplicate else '','platform_match':pid_match[0] if pid_match and not duplicate else '','status':'已存在' if duplicate else ('疑似同作品' if pid_match else '待导入'),'creator_status':'待补资料'})
    out={'batch_id':batch,'created_at':now(),'source':str(source),'count':len(items),'items':items}
    write_json_atomic(manifest_path(batch),out)
    conn.close(); print(json.dumps({'batch_id':batch,'count':len(items),'existing':sum(x['status']=='已存在' for x in items),'suspected':sum(x['status']=='疑似同作品' for x in items),'manifest':str(manifest_path(batch))},ensure_ascii=False,indent=2))

def import_batch(batch, accept_suspected=False):
    mp=manifest_path(batch)
    if not mp.exists(): raise SystemExit(f'找不到扫描清单：{mp}')
    data=json.loads(mp.read_text(encoding='utf-8')); root=INBOX/safe_name(Path(data['source']).name); root.mkdir(parents=True,exist_ok=True)
    dbbak=DB.with_name(f'library_before_source_import_{batch}.sqlite3'); shutil.copy2(DB,dbbak)
    conn=sqlite3.connect(DB); conn.row_factory=sqlite3.Row; imported=0; skipped=0; rows=[]; copied=[]
    try:
        conn.execute('BEGIN')
        for item in data['items']:
            if item['status']=='已存在' or (item['status']=='疑似同作品' and not accept_suspected): skipped+=1; rows.append(item); continue
            src=Path(item['source_path'])
            if not src.exists(): item['status']='源文件不存在'; rows.append(item); continue
            expected=str(item.get('sha256') or '')
            # A prior successful import of this batch is already complete.
            prior=conn.execute('select id,current_path from files where batch_id=? and sha256=? limit 1',(batch,expected)).fetchone()
            if prior and Path(prior['current_path']).is_file() and sha256(Path(prior['current_path']))==expected:
                item.update(status='已复制待整理',current_path=prior['current_path'],file_id=prior['id']); skipped+=1; rows.append(item); continue
            # Another batch may have imported the same content already.
            existing=conn.execute("select id,current_path from files where sha256=? and status!='已永久删除' limit 1",(expected,)).fetchone()
            if existing and Path(existing['current_path']).is_file() and sha256(Path(existing['current_path']))==expected:
                item.update(status='已存在',duplicate_of=existing['id'],duplicate_path=existing['current_path']); skipped+=1; rows.append(item); continue
            dest=root/Path(item['relative_path']); dest.parent.mkdir(parents=True,exist_ok=True)
            if dest.exists():
                if sha256(dest)==expected:
                    item.update(status='已复制待整理',current_path=str(dest)); skipped+=1; rows.append(item); continue
                dest=root/(Path(item['relative_path']).stem+'_'+expected[:8]+dest.suffix)
            shutil.copy2(src,dest); copied.append(dest)
            fid='f_'+uuid.uuid4().hex; creator=None; role='补充来源'; association='待补资料'; status='已复制待整理'
            conn.execute("insert into files(id,batch_id,work_id,creator_id,original_path,current_path,size,mtime_ns,sha256,extension,kind,duplicate_of,target_path,status,author_basis,association_status,backup_status,filename_platform_id,library_role,primary_match_status,video_label,dedup_basis,primary_match_file,classified_at) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(fid,batch,None,creator,item['source_path'],str(dest),item['size'],item['mtime_ns'],item['sha256'],dest.suffix.lower().lstrip('.'),'video',None,str(dest),status,'来源导入，未核实作者',association,'未验证独立备份',item.get('filename_platform_id') or None,role,'待人工确认','待补资料','SHA-256未重复',None,now()))
            item.update(status='已复制待整理',current_path=str(dest),file_id=fid); imported+=1; rows.append(item)
        conn.commit()
    except Exception:
        conn.rollback()
        for p in copied:
            try: p.unlink(missing_ok=True)
            except OSError: pass
        raise
    finally: conn.close()
    data['imported_at']=now(); data['imported']=imported; data['skipped']=skipped; data['items']=rows; write_json_atomic(mp,data); print(json.dumps({'batch_id':batch,'imported':imported,'skipped':skipped,'database_backup':str(dbbak),'manifest':str(mp)},ensure_ascii=False,indent=2))

ap=argparse.ArgumentParser(description='来源视频分批扫描与合并工具'); sub=ap.add_subparsers(dest='cmd',required=True); s=sub.add_parser('scan'); s.add_argument('source'); i=sub.add_parser('import'); i.add_argument('batch'); i.add_argument('--accept-suspected',action='store_true'); args=ap.parse_args()
if args.cmd=='scan': scan(args.source)
else: import_batch(args.batch,args.accept_suspected)

