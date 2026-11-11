"""Durable JSON + SQLite storage for the Douyin harvest pipeline.

SQLite is written first in one transaction; readable JSON mirrors and a
timestamped snapshot are written only after the transaction succeeds.
"""
from __future__ import annotations
import json, sqlite3, time, uuid
from pathlib import Path

SCHEMA = '''
CREATE TABLE IF NOT EXISTS creators(
  url TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT, first_seen TEXT, last_seen TEXT,
  following INTEGER, aliases_json TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS works(
  aweme_id TEXT PRIMARY KEY, creator_url TEXT, creator TEXT, title TEXT,
  publish_time TEXT, url TEXT, verified_aweme INTEGER, read_status TEXT,
  download_status TEXT, local_path TEXT, selection_reason TEXT, download_error TEXT,
  last_cdn_seen_at TEXT, updated_at TEXT, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS metrics(
  id INTEGER PRIMARY KEY AUTOINCREMENT, aweme_id TEXT NOT NULL, observed_at TEXT NOT NULL,
  play_count REAL, digg_count REAL, collect_count REAL, comment_count REAL,
  share_count REAL, source TEXT, UNIQUE(aweme_id, observed_at)
);
CREATE TABLE IF NOT EXISTS updates(
  update_id TEXT PRIMARY KEY, started_at TEXT, finished_at TEXT, mode TEXT,
  status TEXT, creators INTEGER, works INTEGER, downloaded INTEGER, failed INTEGER,
  extinct INTEGER, error TEXT
);
CREATE TABLE IF NOT EXISTS rules(
  version TEXT PRIMARY KEY, created_at TEXT, description TEXT, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshots(
  snapshot_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, state_json_path TEXT NOT NULL,
  works INTEGER, creators INTEGER, sha256 TEXT
);
CREATE INDEX IF NOT EXISTS metrics_work_time ON metrics(aweme_id, observed_at);
CREATE INDEX IF NOT EXISTS works_creator ON works(creator_url);
'''

def _now(): return time.strftime('%Y-%m-%d %H:%M:%S')

def connect(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    c=sqlite3.connect(path, timeout=30)
    c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA synchronous=FULL')
    c.executescript(SCHEMA); return c

def _num(x): return x if isinstance(x,(int,float)) else None

def sync(state: dict, db_path: Path, data_dir: Path):
    now=_now(); creators=state.get('creators') or []; works=state.get('works') or []
    with connect(db_path) as c:
        c.execute('BEGIN')
        for creator in creators:
            url=str(creator.get('url') or '')
            if not url: continue
            c.execute('''INSERT INTO creators(url,name,status,first_seen,last_seen,following,aliases_json)
              VALUES(?,?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET name=excluded.name,status=excluded.status,last_seen=excluded.last_seen,following=excluded.following,aliases_json=excluded.aliases_json''',
              (url,str(creator.get('name') or ''),str(creator.get('status') or 'active'),now,now,1,json.dumps(creator.get('aliases') or [],ensure_ascii=False)))
        for w in works:
            aid=str(w.get('aweme_id') or '')
            if not aid: continue
            payload=json.dumps(w,ensure_ascii=False,separators=(',',':'))
            c.execute('''INSERT INTO works(aweme_id,creator_url,creator,title,publish_time,url,verified_aweme,read_status,download_status,local_path,selection_reason,download_error,last_cdn_seen_at,updated_at,payload_json)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(aweme_id) DO UPDATE SET creator_url=excluded.creator_url,creator=excluded.creator,title=excluded.title,publish_time=excluded.publish_time,url=excluded.url,verified_aweme=excluded.verified_aweme,read_status=excluded.read_status,download_status=excluded.download_status,local_path=excluded.local_path,selection_reason=excluded.selection_reason,download_error=excluded.download_error,last_cdn_seen_at=excluded.last_cdn_seen_at,updated_at=excluded.updated_at,payload_json=excluded.payload_json''',
              (aid,w.get('creator_url',''),w.get('creator',''),w.get('title',''),w.get('publish_time',''),w.get('url',''),1 if w.get('verified_aweme',True) else 0,w.get('read_status',''),w.get('download_status',''),w.get('local_path',''),w.get('selection_reason',''),w.get('download_error',''),w.get('last_cdn_seen_at',''),now,payload))
            vals=(_num(w.get('play_count')),_num(w.get('digg_count')),_num(w.get('collect_count')),_num(w.get('comment_count')),_num(w.get('share_count')))
            if any(x is not None for x in vals):
                c.execute('''INSERT OR IGNORE INTO metrics(aweme_id,observed_at,play_count,digg_count,collect_count,comment_count,share_count,source) VALUES(?,?,?,?,?,?,?,?)''',(aid,now,*vals,'douyin-web'))
        c.execute('INSERT OR REPLACE INTO rules(version,created_at,description,payload_json) VALUES(?,?,?,?)',('精华-v2',now,'分层点赞百分位+近期3条+播放量8000+中位数3倍',json.dumps({'slow_review':'30d/90d','recent':3},ensure_ascii=False)))
        c.execute('INSERT OR REPLACE INTO rules(version,created_at,description,payload_json) VALUES(?,?,?,?)',('标准-v1',now,'按博主作品量分层配额；低播放博主降低播放门槛；近期10条；每位博主至少10条',json.dumps({'ratios':{'<=20':1.0,'21-50':0.8,'51-100':0.7,'101-300':0.6,'>300':0.5},'low_reach_median':8000,'recent':10,'minimum':10},ensure_ascii=False)))
        c.commit()
        db_snapshot=''
        if state.get('snapshot_requested') or state.get('status') in ('完成','失败','已停止'):
            db_snapshot=str(db_path.with_name('采集索引_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]+'.sqlite3'))
            c.execute('VACUUM INTO ?', (db_snapshot,))
    data_dir.mkdir(parents=True,exist_ok=True)
    mirrors={'creators.json':creators,'works.json':works,'updates.json':state.get('updates',[]),'rules.json':{'精华-v2':{'description':'分层点赞百分位+近期3条+播放量8000+中位数3倍'},'标准-v1':{'description':'按博主作品量分层配额；低播放博主降低播放门槛；近期10条；每位博主至少10条'}},'state.json':state}
    for name,obj in mirrors.items():
        tmp=data_dir/(name+'.tmp'); tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8'); tmp.replace(data_dir/name)
    snapfile=''
    if state.get('snapshot_requested') or state.get('status') in ('完成','失败','已停止'):
        snap=data_dir/'snapshots'; snap.mkdir(exist_ok=True)
        sid=time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]; snapfile=str(snap/(sid+'.json')); Path(snapfile).write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
    return db_snapshot, str(snapfile)
