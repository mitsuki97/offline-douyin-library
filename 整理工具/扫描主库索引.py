from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import sqlite3
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

from library import DB, LIB, backup, connect, digest

ROOT = LIB / '关注归档'

def timestamp(path: Path) -> str:
    return datetime.datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec='seconds')

def progress_write(path: Path | None, **data):
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    old = {}
    try:
        old = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        pass
    old.update(data)
    old['updated_at'] = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(old, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)

def columns(conn, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f'pragma table_info({table})')}

def ensure_player_columns(conn):
    existing = columns(conn, 'files')
    additions = {
        'filename_platform_id': "TEXT",
        'library_role': "TEXT DEFAULT '补充来源'",
        'primary_match_status': "TEXT DEFAULT '未分类'",
        'video_label': "TEXT DEFAULT ''",
        'dedup_basis': "TEXT DEFAULT ''",
        'primary_match_file': "TEXT DEFAULT ''",
        'classified_at': "TEXT DEFAULT ''",
    }
    for name, definition in additions.items():
        if name not in existing:
            conn.execute(f'alter table files add column {name} {definition}')
    conn.commit()

def load_creator_data(folder: Path):
    path = folder / '主页作品.json'
    if not path.is_file():
        return {}, None, None
    try:
        records = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(records, list):
            records = records.get('works', records.get('aweme_list', []))
        by_id = {str(x.get('aweme_id')): x for x in records if isinstance(x, dict) and x.get('aweme_id')}
        first = next((x for x in records if isinstance(x, dict)), {})
        return by_id, first.get('creator'), first.get('creator_sec_uid')
    except Exception:
        return {}, None, None

def creator_for(conn, folder: Path, display_name: str | None, sec_uid: str | None, cache: dict):
    key = str(folder)
    if key in cache:
        return cache[key]
    cid = None
    if sec_uid:
        row = conn.execute('select id,name,platform_id from creators where platform_id=? limit 1', (sec_uid,)).fetchone()
        if row:
            cid = row['id']
    if not cid and display_name:
        matches = conn.execute('select id,platform_id from creators where name=?', (display_name,)).fetchall()
        if len(matches) == 1 and (not sec_uid or not matches[0]['platform_id'] or matches[0]['platform_id'] == sec_uid):
            cid = matches[0]['id']
            if sec_uid and not matches[0]['platform_id']:
                conn.execute("update creators set platform_id=?,identity_status='主页作品资料关联，待核实账号' where id=?", (sec_uid, cid))
    if not cid:
        cid = 'C' + hashlib.sha256(key.encode()).hexdigest()[:12]
        conn.execute('insert or ignore into creators(id,name,platform_id,identity_status,aliases_json) values(?,?,?,?,?)',
                     (cid, display_name, sec_uid, '主库目录推定，账号待核实', json.dumps([display_name] if display_name else [], ensure_ascii=False)))
    cache[key] = cid
    return cid

def upsert_work_and_metadata(conn, pid: str, creator_id: str, title: str, record: dict | None, metadata_path: Path | None, known_work_id: str | None = None):
    work = conn.execute('select id,creator_id,title,published_at from works where id=?', (known_work_id,)).fetchone() if known_work_id else None
    if not work and pid:
        work = conn.execute('select id,creator_id,title,published_at from works where platform_id=? order by id limit 1', (pid,)).fetchone()
    if work:
        wid = work['id']
    else:
        wid = 'W' + uuid.uuid4().hex[:16]
    meta_title = str(record.get('title') or '').strip() if record else ''
    final_title = meta_title or title or '标题未知'
    published = str(record.get('publish_time') or '') if record else ''
    tags = json.dumps(re.findall(r'#([^#\s]+)', final_title), ensure_ascii=False)
    if work:
        if meta_title and (not work['title'] or work['title'] in (pid, '标题未知', 'douyin_video')):
            conn.execute('update works set title=?,published_at=coalesce(nullif(published_at,\'\'),?),tags_json=? where id=?', (final_title, published, tags, wid))
    else:
        conn.execute('insert into works(id,creator_id,title,platform_id,url,published_at,tags_json,identity_status) values(?,?,?,?,?,?,?,?)',
                     (wid, creator_id, final_title, pid or None, 'https://www.douyin.com/video/' + pid if pid else None, published or None, tags, '主页作品资料' if record else '作品ID来自文件名'))
    if record and record.get('verified_aweme') and metadata_path:
        observed = timestamp(metadata_path)
        locator = f'主页作品.json:aweme_id={pid}:mtime_ns={metadata_path.stat().st_mtime_ns}'
        values = [('点赞量', 'digg_count'), ('收藏量', 'collect_count'), ('评论量', 'comment_count'), ('分享量', 'share_count'), ('播放量', 'play_count')]
        evidence = json.dumps({'aweme_id': pid, 'verified_aweme': True}, ensure_ascii=False)
        for metric, key in values:
            value = record.get(key)
            if value is None:
                continue
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            if not conn.execute('select 1 from metrics where work_id=? and metric=? and source_file=? and source_locator=?',
                                (wid, metric, str(metadata_path), locator)).fetchone():
                conn.execute('insert into metrics(work_id,metric,value,observed_at,recorded_at,source,source_file,source_locator,value_kind,confidence,evidence) values(?,?,?,?,?,?,?,?,?,?,?)',
                             (wid, metric, value, observed, datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
                              '关注归档主页作品 JSON', str(metadata_path), locator, '本地历史快照', '作品ID精确匹配', evidence))
    return wid, final_title, record.get('duration_ms') if record else None

def run(progress: Path | None, stop_file: Path | None = None):
    if not ROOT.is_dir():
        raise FileNotFoundError(f'主库目录不存在：{ROOT}')
    progress_write(progress, status='运行中', phase='查找主库文件', worker_pid=os.getpid(), processed=0, total=0, percent=0)
    paths = sorted((p for p in ROOT.rglob('*.mp4') if p.is_file()), key=lambda p: str(p).casefold())
    if stop_file and stop_file.exists():
        progress_write(progress, status='已停止', phase='用户停止', current='')
        return
    conn = connect()
    try:
        batch = 'primary_index_' + datetime.datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6]
        progress_write(progress, status='运行中', phase='备份数据库', batch_id=batch)
        backup(conn, f'before_{batch}.sqlite3')
        ensure_player_columns(conn)
        known = {r['current_path']: dict(r) for r in conn.execute('select id,current_path,size,mtime_ns,work_id,creator_id from files')}
        todo = []
        indexed_paths = []
        stale = []
        folders = {}
        creators = {}
        for n, path in enumerate(paths, 1):
            if stop_file and stop_file.exists():
                progress_write(progress, status='已停止', phase='用户停止', current='')
                return
            if n == 1 or n % 200 == 0:
                progress_write(progress, status='运行中', phase='检查已登记文件', processed=n, total=len(paths), percent=round(n*100/len(paths),1), current=str(path))
            rel = path.relative_to(ROOT)
            folder = ROOT / rel.parts[0] if len(rel.parts) > 1 else ROOT
            if folder not in folders:
                metadata = folder / '主页作品.json'
                records, name, sec_uid = load_creator_data(folder) if folder != ROOT else ({}, None, None)
                inferred = folder.name.split('__', 1)[0] if folder != ROOT else None
                folders[folder] = (records, name or inferred, sec_uid, metadata if metadata.is_file() else None)
            old = known.get(str(path))
            stat = path.stat()
            if old:
                if old['size'] != stat.st_size or old['mtime_ns'] != stat.st_mtime_ns:
                    stale.append(str(path))
                else:
                    indexed_paths.append((path, old, folder))
            else:
                todo.append(path)
        for n, (path, old, folder) in enumerate(indexed_paths, 1):
                if stop_file and stop_file.exists():
                    conn.commit()
                    progress_write(progress, status='已停止', phase='用户停止', current='')
                    return
                if n == 1 or n % 50 == 0:
                    conn.commit()
                    progress_write(progress, status='运行中', phase='更新已登记文件的资料', processed=n, total=len(indexed_paths), percent=round(n*100/len(indexed_paths),1), current=str(path))
                records, creator_name, sec_uid, metadata_path = folders[folder]
                pid = path.stem if re.fullmatch(r'\d{18,20}', path.stem) else ''
                record = records.get(pid) if pid else None
                cid = old['creator_id'] or creator_for(conn, folder, creator_name, sec_uid, creators)
                wid = old['work_id']
                if pid and (record or not wid):
                    wid, _, duration = upsert_work_and_metadata(conn, pid, cid, '', record, metadata_path, wid)
                    conn.execute('update files set work_id=?,creator_id=?,filename_platform_id=? where id=?', (wid,cid,pid or None,old['id']))
                    if duration:
                        try:
                            conn.execute('insert or ignore into media(file_id,duration,status,method,checked_at) values(?,?,?,?,?)',
                                         (old['id'],float(duration)/1000,'未检查','关注归档主页作品元数据',datetime.datetime.now().astimezone().isoformat(timespec='seconds')))
                        except (TypeError, ValueError):
                            pass
                conn.execute("update files set library_role='主库',primary_match_status='主库自身' where id=?", (old['id'],))
        conn.execute('insert into batches(id,created_at,scope_json) values(?,?,?)',
                     (batch, datetime.datetime.now().astimezone().isoformat(timespec='seconds'), json.dumps([str(ROOT)], ensure_ascii=False)))
        conn.commit()
        progress_write(progress, batch_id=batch, status='运行中', phase='登记主库视频', total=len(todo), processed=0, percent=0,
                       added=0, skipped=len(indexed_paths), stale_count=len(stale), stale_examples=stale[:20], current='',
                       backup=str(LIB/'索引'/'备份'/f'before_{batch}.sqlite3'))

        def inspect(path):
            before = path.stat()
            file_hash = digest(path)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise RuntimeError(f'扫描期间文件发生变化：{path}')
            return path, after, file_hash

        added = 0
        processed = 0
        errors = []
        stopped = False

        def register(path, stat, file_hash):
                rel = path.relative_to(ROOT)
                folder = ROOT / rel.parts[0] if len(rel.parts) > 1 else ROOT
                records, creator_name, sec_uid, metadata_path = folders[folder]
                cid = creator_for(conn, folder, creator_name, sec_uid, creators)
                pid = path.stem if re.fullmatch(r'\d{18,20}', path.stem) else ''
                record = records.get(pid) if pid else None
                fallback_title = re.sub(r'^\d+[ _]+', '', path.stem)
                wid, title, duration = upsert_work_and_metadata(conn, pid, cid, fallback_title, record, metadata_path)
                fid = 'F' + uuid.uuid4().hex[:16]
                conn.execute('''insert into files(id,batch_id,work_id,creator_id,original_path,current_path,size,mtime_ns,sha256,extension,kind,status,author_basis,association_status,filename_platform_id,library_role,primary_match_status,video_label,dedup_basis,primary_match_file,classified_at)
                    values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (fid,batch,wid,cid,str(path),str(path),stat.st_size,stat.st_mtime_ns,file_hash,'.mp4','video','已清点',
                     '关注归档目录及主页作品元数据','作品ID精确匹配' if pid else '未关联作品',pid or None,'主库','主库自身','',
                     '主库索引扫描','',datetime.datetime.now().astimezone().isoformat(timespec='seconds')))
                if duration:
                    try:
                        conn.execute('insert or ignore into media(file_id,duration,status,method,checked_at) values(?,?,?,?,?)',
                                     (fid,float(duration)/1000,'未检查','关注归档主页作品元数据',datetime.datetime.now().astimezone().isoformat(timespec='seconds')))
                    except (TypeError, ValueError):
                        pass
                return fid

        with ThreadPoolExecutor(max_workers=4) as pool:
            iterator = iter(todo)
            pending = set()
            for _ in range(4):
                if stop_file and stop_file.exists():
                    stopped = True
                    break
                path = next(iterator, None)
                if path is not None:
                    pending.add(pool.submit(inspect, path))
            while pending and not stopped:
                if stop_file and stop_file.exists():
                    stopped = True
                    for future in pending:
                        future.cancel()
                    break
                done, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
                for future in done:
                    file_path = ''
                    conn.execute('savepoint primary_index_file')
                    try:
                        path, stat, file_hash = future.result()
                        file_path = str(path)
                        register(path, stat, file_hash)
                        conn.execute('release primary_index_file')
                        added += 1
                    except Exception as exc:
                        conn.execute('rollback to primary_index_file')
                        conn.execute('release primary_index_file')
                        creators.clear()
                        errors.append(str(exc))
                    processed += 1
                    if processed % 10 == 0 or processed == len(todo):
                        conn.commit()
                        progress_write(progress, status='运行中', phase='登记主库视频', total=len(todo), processed=processed,
                                       percent=round(processed*100/len(todo),1) if todo else 100, added=added,
                                       skipped=len(indexed_paths), stale_count=len(stale), error_count=len(errors),
                                       error_examples=errors[:20], current=file_path)
                    if not (stop_file and stop_file.exists()):
                        next_path = next(iterator, None)
                        if next_path is not None:
                            pending.add(pool.submit(inspect, next_path))
                if stop_file and stop_file.exists():
                    stopped = True
                    for future in pending:
                        future.cancel()
        conn.commit()
        if stopped:
            status = '已停止'
            phase = '用户停止'
        elif errors:
            status = '完成（有文件跳过）'
            phase = '完成'
        else:
            status = '完成'
            phase = '完成'
        summary = {'batch_id':batch,'primary_mp4_count':len(paths),'added':added,'already_indexed':len(indexed_paths),
                   'stale_skipped':len(stale),'stale_examples':stale[:20],
                   'creator_count':len(folders),'error_count':len(errors),'error_examples':errors[:20],
                   'backup':str(LIB/'索引'/'备份'/f'before_{batch}.sqlite3')}
        progress_write(progress, status=status, phase=phase, total=len(todo), processed=processed,
                       percent=round(processed*100/len(todo),1) if todo else 100, current='', added=added, summary=summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    except Exception as exc:
        conn.rollback()
        progress_write(progress, status='失败', phase='失败', error=str(exc))
        raise
    finally:
        conn.close()

def check():
    paths = [p for p in ROOT.rglob('*.mp4') if p.is_file()]
    conn = sqlite3.connect(f'file:{DB.as_posix()}?mode=ro', uri=True)
    try:
        indexed = {r[0] for r in conn.execute('select current_path from files where current_path like ?', (str(ROOT)+'%',))}
        missing = [p for p in paths if str(p) not in indexed]
        target = ROOT / '97吨三文鱼__2fc76f5b'
        data, _, _ = load_creator_data(target)
        target_videos = [p for p in target.glob('*.mp4')]
        matched = sum(p.stem in data for p in target_videos)
        print(json.dumps({'primary_mp4_count':len(paths),'already_indexed':len(paths)-len(missing),'missing_from_index':len(missing),
                          'target_folder':str(target),'target_mp4_count':len(target_videos),'target_metadata_matches':matched},ensure_ascii=False,indent=2))
    finally:
        conn.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='只登记关注归档中的 MP4 到播放器索引，不移动或删除媒体')
    parser.add_argument('--progress', type=Path)
    parser.add_argument('--stop-file', type=Path)
    parser.add_argument('--check', action='store_true', help='只读核对主库索引覆盖情况')
    args = parser.parse_args()
    check() if args.check else run(args.progress, args.stop_file)

