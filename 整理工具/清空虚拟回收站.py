from __future__ import annotations
import argparse, os, shutil, sqlite3, uuid
from datetime import datetime
from pathlib import Path
BASE=Path(__file__).resolve().parent.parent; LIB=BASE/'本地视频库'; DB=LIB/'索引'/'library.sqlite3'; RECYCLE=(LIB/'虚拟回收站').resolve()
def now(): return datetime.now().astimezone().isoformat(timespec='seconds')
ap=argparse.ArgumentParser(); ap.add_argument('--apply',action='store_true'); args=ap.parse_args()
backup=DB.with_name('library_before_recycle_purge_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.sqlite3'); shutil.copy2(DB,backup)
conn=sqlite3.connect(DB); conn.execute('pragma foreign_keys=off'); paths=[str(p) for p in RECYCLE.rglob('*') if p.is_file()] if RECYCLE.exists() else []
if not args.apply: print({'preview_files':len(paths),'backup':str(backup)}); raise SystemExit(0)
prefix=str(RECYCLE)+os.sep
ids=[r[0] for r in conn.execute('select id from files where current_path like ?',(prefix+'%',))]
for table,col in [('media','file_id'),('moves','file_id')]:
    if ids: conn.executemany(f'delete from {table} where {col}=?',[(x,) for x in ids])
if ids: conn.executemany('delete from similar where a=? or b=?',[(x,x) for x in ids]); conn.executemany('delete from files where id=?',[(x,) for x in ids])
conn.commit(); conn.close();
if RECYCLE.exists(): shutil.rmtree(RECYCLE)
print({'deleted_files':len(paths),'deleted_index_records':len(ids),'backup':str(backup),'applied':True})
