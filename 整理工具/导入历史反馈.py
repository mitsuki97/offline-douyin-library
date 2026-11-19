from __future__ import annotations
import datetime, hashlib, json, os, sqlite3, sys
from pathlib import Path

import openpyxl

BASE = Path(__file__).resolve().parent.parent
LIB = BASE / '本地视频库'
DB = LIB / '索引' / 'library.sqlite3'
LIKE_XLSX = LIB / '其他资料' / '20261105_153218_d9977b' / '我喜欢的作品_2024-04-29_16-20-36__F3a6b986287ad4124.xlsx'
FAV_XLSX = LIB / '其他资料' / '20261105_153218_d9977b' / '我收藏的作品_2024-04-29_16-22-59__F21685a8974084178.xlsx'

def read_xlsx(path: Path):
    if not path.is_file(): return []
    ws = openpyxl.load_workbook(path, read_only=True, data_only=True).active
    rows = list(ws.iter_rows(values_only=True))
    if not rows: return []
    headers = [str(x or '').strip() for x in rows[0]]
    pos = {h:i for i,h in enumerate(headers)}
    out=[]
    for rownum, row in enumerate(rows[1:], 2):
        pid = str(row[pos['作品ID']] or '').strip() if '作品ID' in pos and pos['作品ID'] < len(row) else ''
        if not pid: continue
        out.append({'platform_id':pid, 'row_number':rownum, 'title':str(row[pos.get('标题',3)] or ''), 'creator':str(row[pos.get('作者昵称',5)] or ''), 'url':str(row[pos.get('作品链接',2)] or '')})
    return out

def import_history():
    con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
    con.execute('''create table if not exists history_imports(
        id text primary key, imported_at text not null, source_path text not null,
        event_type text not null, total_count integer not null, matched_count integer not null,
        unmatched_count integer not null, note text default '')''')
    con.execute('''create table if not exists history_records(
        id text primary key, import_id text not null, event_type text not null,
        platform_id text not null, work_id text, file_id text, row_number integer,
        title text, creator text, url text, status text not null, created_at text not null)''')
    now=datetime.datetime.now().astimezone().isoformat(timespec='seconds')
    result=[]
    for event_type,path in [('like',LIKE_XLSX),('favorite',FAV_XLSX)]:
        source_hash=hashlib.sha1(str(path).encode()).hexdigest()[:12]
        import_id=f'history_{event_type}_{source_hash}'
        rows=read_xlsx(path)
        # Re-import is idempotent: remove only records created by this source/type.
        con.execute('delete from history_records where import_id=?',(import_id,))
        con.execute("delete from feedback where json_extract(extra_json,'$.history_import_id')=?",(import_id,))
        matched=unmatched=0
        for r in rows:
            work=con.execute('select id from works where platform_id=? order by id limit 1',(r['platform_id'],)).fetchone()
            file=con.execute("""select id from files where work_id=? and kind='video' and status!='已永久删除' and duplicate_of is null
                order by case when coalesce(library_role,'')='主库' then 0 else 1 end, current_path limit 1""",(work['id'],)).fetchone() if work else None
            status='matched' if work and file else 'unmatched'
            if status=='matched': matched+=1
            else: unmatched+=1
            rid=hashlib.sha1(f'{import_id}:{r["platform_id"]}'.encode()).hexdigest()
            if status=='matched':
                con.execute('insert or replace into history_records values(?,?,?,?,?,?,?,?,?,?,?,?)',(rid,import_id,event_type,r['platform_id'],work['id'],file['id'],r['row_number'],r['title'],r['creator'],r['url'],status,now))
                extra={'file_id':file['id'],'creator_id':None,'history_import_id':import_id,'source_path':str(path),'row_number':r['row_number'],'historical':True}
                con.execute('insert into feedback(id,work_id,event_at,event_type,watch_ms,session_id,recommender_version,extra_json) values(?,?,?,?,?,?,?,?)',(f'history_{rid}',work['id'],now,event_type,0,'history-import','history-v1',json.dumps(extra,ensure_ascii=False)))
        con.execute('insert or replace into history_imports values(?,?,?,?,?,?,?,?)',(import_id,now,str(path),event_type,len(rows),matched,unmatched,'未匹配记录不导入'))
        result.append({'import_id':import_id,'event_type':event_type,'total':len(rows),'matched':matched,'unmatched':unmatched})
    con.commit(); con.close(); return result

if __name__=='__main__': print(json.dumps(import_history(),ensure_ascii=False,indent=2))

