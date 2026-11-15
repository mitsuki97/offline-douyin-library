from __future__ import annotations
import argparse, json, sqlite3
from datetime import datetime
from pathlib import Path

BASE=Path(__file__).resolve().parent.parent; LIB=BASE/'本地视频库'; HARVEST=LIB/'关注归档'; JSON_STATE=HARVEST/'采集进度.json'; DB=LIB/'索引'/'library.sqlite3'
def now(): return datetime.now().astimezone().isoformat(timespec='seconds')
ap=argparse.ArgumentParser(description='只读检查下载器 JSON 与管理器 SQLite 的路径同步状态'); ap.add_argument('--output'); args=ap.parse_args()
state=json.loads(JSON_STATE.read_text(encoding='utf-8')); works=state.get('works') or []
conn=sqlite3.connect(DB); conn.row_factory=sqlite3.Row
indexed={str(r['filename_platform_id']):dict(r) for r in conn.execute("select id,filename_platform_id,current_path,status from files where filename_platform_id is not null and filename_platform_id!=''")}
items=[]
for w in works:
    aid=str(w.get('aweme_id') or ''); path=str(w.get('local_path') or '')
    if not aid or not path: continue
    row=indexed.get(aid); exists=Path(path).exists(); manager_path=str(row['current_path']) if row else ''
    items.append({'aweme_id':aid,'creator':w.get('creator',''),'json_path':path,'json_exists':exists,'manager_file_id':row['id'] if row else None,'manager_path':manager_path,'manager_exists':bool(row and Path(manager_path).exists()),'path_match':bool(row and manager_path==path),'status':'一致' if row and manager_path==path and exists else ('下载器记录存在但管理器未同步' if not row else ('文件路径已整理' if exists and manager_path!=path else '路径不存在'))})
conn.close()
result={'checked_at':now(),'json_source':str(JSON_STATE),'manager_db':str(DB),'json_work_count':len(works),'checked_count':len(items),'consistent':sum(x['status']=='一致' for x in items),'manager_missing':sum(x['status']=='下载器记录存在但管理器未同步' for x in items),'moved_paths':sum(x['status']=='文件路径已整理' for x in items),'missing_paths':sum(x['status']=='路径不存在' for x in items),'items':items}
out=Path(args.output) if args.output else LIB/'索引'/'下载器同步检查.json'; out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps({k:v for k,v in result.items() if k!='items'},ensure_ascii=False,indent=2))
