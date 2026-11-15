from __future__ import annotations
import argparse, hashlib, json, re, shutil, sqlite3, uuid
from datetime import datetime
from pathlib import Path

BASE=Path(__file__).resolve().parent.parent; LIB=BASE/'本地视频库'; DB=LIB/'索引'/'library.sqlite3'; PRIMARY=LIB/'关注归档'; RECYCLE=LIB/'虚拟回收站'
SOURCES=[BASE/'下载的视频'/'抖音下载',BASE/'下载的视频'/'抖音下载2',BASE/'下载的视频'/'快斗下载器'/'快抖下载器',LIB/'博主',LIB/'待导入']
EXT={'.mp4','.mov','.mkv','.webm','.avi','.m4v','.flv','.ts'}
def now(): return datetime.now().astimezone().isoformat(timespec='seconds')
def sha(p):
 h=hashlib.sha256();
 with p.open('rb') as f:
  while b:=f.read(1024*1024): h.update(b)
 return h.hexdigest()
def pid(name):
 m=re.search(r'(?<!\d)(\d{15,25})(?!\d)',name); return m.group(1) if m else ''
def vids(root): return [p for p in root.rglob('*') if p.is_file() and p.suffix.lower() in EXT]
def relabel(c,path,**kw):
 row=c.execute('select id from files where current_path=? or original_path=?',(str(path),str(path))).fetchone()
 if not row: return None
 sets=[]; args=[]
 for k,v in kw.items(): sets.append(f'{k}=?'); args.append(v)
 args.append(row[0]); c.execute('update files set '+','.join(sets)+' where id=?',args); return row[0]
def run(apply=False, progress=None):
 def report(**kw):
  if progress:
   progress.parent.mkdir(parents=True,exist_ok=True); old={}
   try: old=json.loads(progress.read_text(encoding='utf-8'))
   except Exception: pass
   old.update(kw); progress.write_text(json.dumps(old,ensure_ascii=False),encoding='utf-8')
 batch='primary_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]; primary=[p for p in vids(PRIMARY)]; ph={}; pp={}
 report(status='运行中',phase='扫描主库',batch_id=batch,total=len(primary),processed=0,percent=0,current='')
 for n,p in enumerate(primary,1):
  h=sha(p); ph.setdefault(h,p); x=pid(p.stem)
  if x: pp.setdefault(x,p)
  if n==1 or n%20==0 or n==len(primary): report(phase='扫描主库',processed=n,total=len(primary),percent=round(n*100/len(primary),1) if primary else 100,current=str(p))
 conn=sqlite3.connect(DB); conn.row_factory=sqlite3.Row; result={'batch_id':batch,'created_at':now(),'primary_root':str(PRIMARY),'sources':[str(x) for x in SOURCES],'primary_count':len(primary),'items':[]}
 total_source=sum(len(vids(root)) for root in SOURCES if root.exists()); done=0; report(phase='比对补充来源',processed=0,total=total_source,percent=0,current='')
 for root in SOURCES:
  if not root.exists(): continue
  for p in vids(root):
   h=sha(p); x=pid(p.stem); match=ph.get(h); basis='SHA-256完全一致' if match else ''
   if not match and x and x in pp: match=pp[x]; basis='作品ID一致（文件不同）'
   item={'source_path':str(p),'source_root':str(root),'sha256':h,'filename_platform_id':x,'size':p.stat().st_size,'match_path':str(match) if match else '','basis':basis,'action':'移入虚拟回收站' if match else '标记疑似绝版'}
   fid=relabel(conn,p,library_role='补充来源',primary_match_status='主库已找到' if match else '主库未发现',video_label='主库已有重复' if match else '疑似绝版，待核对',dedup_basis=basis or '主库未找到 SHA-256、作品ID匹配',classified_at=now())
   item['file_id']=fid
   if match:
    mrow=conn.execute('select id from files where current_path=?',(str(match),)).fetchone(); item['primary_file_id']=mrow[0] if mrow else None
    if fid and mrow: conn.execute('update files set duplicate_of=?,status=? where id=?',(mrow[0],'待移入虚拟回收站' if apply else '重复候选',fid))
   result['items'].append(item)
   done+=1
   if done==1 or done%20==0 or done==total_source: report(phase='比对补充来源',processed=done,total=total_source,percent=round(done*100/total_source,1) if total_source else 100,current=str(p))
 if apply:
  for item in result['items']:
   if item['action']!='移入虚拟回收站': continue
   src=Path(item['source_path']); dest=RECYCLE/batch/src.name; dest.parent.mkdir(parents=True,exist_ok=True)
   if dest.exists(): dest=dest.with_name(dest.stem+'_'+item['sha256'][:8]+dest.suffix)
   shutil.move(str(src),str(dest)); item['recycle_path']=str(dest); conn.execute('update files set current_path=?,status=?,backup_status=? where id=?',(str(dest),'已移入虚拟回收站','已保留虚拟回收站副本',item['file_id']))
  conn.commit()
 else: conn.rollback()
 conn.close(); out=LIB/'索引'/'主库优先整理'; out.mkdir(parents=True,exist_ok=True); (out/f'{batch}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
 summary={'batch_id':batch,'primary_count':len(primary),'supplement_count':len(result['items']),'duplicate_count':sum(x['action']=='移入虚拟回收站' for x in result['items']),'suspected_count':sum(x['action']=='标记疑似绝版' for x in result['items']),'applied':apply,'report':str(out/f'{batch}.json')}; report(status='完成',phase='完成',percent=100,processed=total_source,total=total_source,current='',summary=summary); print(json.dumps(summary,ensure_ascii=False,indent=2))
ap=argparse.ArgumentParser(); ap.add_argument('--apply',action='store_true'); ap.add_argument('--progress'); args=ap.parse_args(); run(args.apply,Path(args.progress) if args.progress else None)
