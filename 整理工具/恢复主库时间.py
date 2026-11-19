"""为主库文件生成或执行修改时间恢复计划。

默认只生成计划，不改变文件。只有显式传入 --apply 才会修改时间，且每个文件
都会在修改前重新校验大小和 SHA-256；计划同时保存恢复前时间，便于撤回。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sqlite3
import uuid
from pathlib import Path

from library import DB, LIB

ROOT = LIB / '关注归档'
PLAN_DIR = LIB / '索引' / '主库优先整理' / 'mtime_plans'


def now():
    return dt.datetime.now().astimezone().isoformat(timespec='seconds')


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read_candidates():
    if not ROOT.is_dir():
        raise FileNotFoundError(f'主库目录不存在：{ROOT}')
    conn = sqlite3.connect(f'file:{DB.as_posix()}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "select id,current_path,size,mtime_ns,sha256 from files "
            "where current_path like ? and lower(current_path) like '%.mp4'",
            (str(ROOT) + '%',),
        ).fetchall()
    finally:
        conn.close()
    candidates = []
    skipped = []
    for row in rows:
        path = Path(row['current_path'])
        if not path.is_file():
            skipped.append({'file_id': row['id'], 'path': str(path), 'reason': '路径不存在'})
            continue
        stat = path.stat()
        if stat.st_size != row['size'] or stat.st_mtime_ns == row['mtime_ns']:
            if stat.st_mtime_ns == row['mtime_ns']:
                skipped.append({'file_id': row['id'], 'path': str(path), 'reason': '时间已一致'})
            else:
                skipped.append({'file_id': row['id'], 'path': str(path), 'reason': '文件大小变化'})
            continue
        if not row['sha256']:
            skipped.append({'file_id': row['id'], 'path': str(path), 'reason': '索引没有 SHA-256'})
            continue
        candidates.append({
            'file_id': row['id'], 'path': str(path),
            'size': stat.st_size, 'sha256': row['sha256'],
            'current_mtime_ns': stat.st_mtime_ns,
            'target_mtime_ns': row['mtime_ns'],
        })
    return candidates, skipped


def create_plan():
    candidates, skipped = read_candidates()
    plan_id = 'mtime_' + dt.datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6]
    plan = {
        'plan_id': plan_id,
        'created_at': now(),
        'root': str(ROOT),
        'read_only': True,
        'requires_explicit_apply': True,
        'candidate_count': len(candidates),
        'skipped_count': len(skipped),
        'candidates': candidates,
        'skipped': skipped,
    }
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    output = PLAN_DIR / f'{plan_id}.json'
    output.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
    plan['plan_file'] = str(output)
    print(json.dumps({'plan_id': plan_id, 'candidate_count': len(candidates),
                      'skipped_count': len(skipped), 'plan_file': str(output)},
                     ensure_ascii=False, indent=2))
    return output


def apply_plan(path: Path):
    plan = json.loads(path.read_text(encoding='utf-8'))
    if not plan.get('requires_explicit_apply'):
        raise ValueError('恢复计划缺少安全标记，拒绝执行')
    applied, failed = [], []
    for item in plan.get('candidates', []):
        file_path = Path(item['path']).resolve()
        try:
            if not file_path.is_relative_to(ROOT.resolve()):
                raise RuntimeError('路径不在主库范围内')
            stat = file_path.stat()
            if stat.st_size != item['size'] or sha256(file_path) != item['sha256']:
                raise RuntimeError('文件大小或 SHA-256 已变化')
            before = stat.st_mtime_ns
            os.utime(file_path, ns=(stat.st_atime_ns, item['target_mtime_ns']))
            after = file_path.stat().st_mtime_ns
            if after != item['target_mtime_ns']:
                raise RuntimeError('写入后时间戳与目标不一致')
            applied.append({'path': str(file_path), 'before_mtime_ns': before,
                            'after_mtime_ns': after, 'sha256': item['sha256']})
        except Exception as exc:
            failed.append({'path': str(file_path), 'reason': str(exc)})
    result = {
        'plan_id': plan.get('plan_id'), 'applied_at': now(),
        'applied_count': len(applied), 'failed_count': len(failed),
        'applied': applied, 'failed': failed,
        'rollback_possible': True,
    }
    output = path.with_name(path.stem + '_applied.json')
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('plan_id', 'applied_at', 'applied_count', 'failed_count', 'rollback_possible')},
                     ensure_ascii=False, indent=2))


def rollback(path: Path):
    record = json.loads(path.read_text(encoding='utf-8'))
    reverted, failed = [], []
    for item in record.get('applied', []):
        file_path = Path(item['path']).resolve()
        try:
            if not file_path.is_relative_to(ROOT.resolve()):
                raise RuntimeError('路径不在主库范围内')
            stat = file_path.stat()
            if sha256(file_path) != item['sha256']:
                raise RuntimeError('文件 SHA-256 已变化')
            if stat.st_mtime_ns != item['after_mtime_ns']:
                raise RuntimeError('文件修改时间已被其他操作改变')
            os.utime(file_path, ns=(stat.st_atime_ns, item['before_mtime_ns']))
            reverted.append({'path': str(file_path), 'restored_mtime_ns': file_path.stat().st_mtime_ns})
        except Exception as exc:
            failed.append({'path': str(file_path), 'reason': str(exc)})
    result = {'plan_id': record.get('plan_id'), 'rolled_back_at': now(),
              'reverted_count': len(reverted), 'failed_count': len(failed),
              'reverted': reverted, 'failed': failed}
    output = path.with_name(path.stem + '_rollback.json')
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('plan_id', 'rolled_back_at', 'reverted_count', 'failed_count')},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='安全恢复关注归档视频的修改时间')
    parser.add_argument('--plan', type=Path, help='已有恢复计划 JSON')
    parser.add_argument('--apply', action='store_true', help='明确执行计划中的时间恢复')
    parser.add_argument('--rollback', type=Path, help='按执行记录撤回修改时间')
    args = parser.parse_args()
    if args.rollback:
        rollback(args.rollback.resolve())
    elif args.apply:
        if not args.plan:
            raise SystemExit('--apply 必须同时提供 --plan')
        apply_plan(args.plan.resolve())
    else:
        create_plan()

