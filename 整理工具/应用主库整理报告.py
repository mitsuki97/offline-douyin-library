from __future__ import annotations
import argparse, json, shutil, sqlite3
from datetime import datetime
from pathlib import Path

BASE=Path(__file__).resolve().parent.parent
LIB=BASE/'本地视频库'; DB=LIB/'索引'/'library.sqlite3'; RECYCLE=LIB/'虚拟回收站'
def now(): return datetime.now().astimezone().isoformat(timespec='seconds')
ap=argparse.ArgumentParser(); ap.add_argument('report'); args=ap.parse_args()
rp=Path(args.report).resolve(); data=json.loads(rp.read_text(encoding='utf-8')); batch=data['batch_id']; backup=DB.with_name(f'library_before_apply_{batch}.sqlite3'); shutil.copy2(DB,backup)
conn=sqlite3.connect(DB); conn.row_factory=sqlite3.Row; moved=marked=missing=0; recycle=RECYCLE/batch; recycle.mkdir(parents=True,exist_ok=True)
for n,item in enumerate(data.get('items',[]),1):
    src=Path(item['source_path'])
    fid=item.get('file_id')
    if item.get('action')=='移入虚拟回收站':
        if not src.exists(): missing+=1; continue
        dest=recycle/src.name
        if dest.exists(): dest=recycle/(src.stem+'_'+str(item.get('sha256',''))[:8]+src.suffix)
        shutil.move(str(src),str(dest)); moved+=1
        if fid: conn.execute("update files set current_path=?,status='已移入虚拟回收站',backup_status='已保留虚拟回收站副本' where id=?",(str(dest),fid))
    elif item.get('action')=='标记疑似绝版':
        if fid:
            conn.execute("update files set library_role='补充来源',primary_match_status='主库未发现',video_label='疑似绝版，待核对',dedup_basis='主库未找到 SHA-256、作品ID匹配',classified_at=? where id=?",(now(),fid)); marked+=1
    if n%100==0: print(f'处理 {n}/{len(data.get("items",[]))}',flush=True)
conn.commit(); conn.close(); data.update(applied=True,applied_at=now(),database_backup=str(backup),recycle_path=str(recycle),moved_count=moved,marked_count=marked,missing_count=missing); rp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps({'applied':True,'moved_count':moved,'marked_count':marked,'missing_count':missing,'backup':str(backup),'recycle':str(recycle)},ensure_ascii=False,indent=2))
