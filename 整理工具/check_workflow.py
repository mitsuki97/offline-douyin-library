"""Exercise destructive workflow against disposable fixtures, never the user's files."""
import tempfile
from pathlib import Path
import library as m
from types import SimpleNamespace

with tempfile.TemporaryDirectory(prefix='local_video_workflow_') as tmp:
    m.BASE=Path(tmp); m.SOURCE=m.BASE/'下载的视频'; m.LIB=m.BASE/'本地视频库'
    m.ALLOWED=[m.SOURCE/'抖音下载',m.SOURCE/'抖音下载2']; m.EXCLUDED=m.SOURCE/'快抖下载器-相对较老'
    m.DB=m.LIB/'索引'/'library.sqlite3'
    folder=m.ALLOWED[1]/'测试作者'; folder.mkdir(parents=True)
    originals={folder/'同名_1234567890123456789.mp4':b'fixture-a',folder/'不同作品_2234567890123456789.mp4':b'fixture-a',folder/'=另一份.mp4':b'fixture-b'}
    m.EXCLUDED.mkdir(); excluded=m.EXCLUDED/'排除.mp4'; excluded.write_bytes(b'untouched')
    for p,b in originals.items(): p.write_bytes(b)
    c=m.connect(); m.scan(c); m.plan(c)
    assert c.execute('SELECT count(*) FROM files').fetchone()[0]==3
    assert c.execute('SELECT count(*) FROM works').fetchone()[0]==3,'Different platform IDs must survive even for identical downloaded bytes'
    assert c.execute('SELECT count(*) FROM files WHERE duplicate_of IS NOT NULL').fetchone()[0]==1
    row=c.execute('SELECT * FROM files LIMIT 1').fetchone()
    dst=Path(row['target_path']); dst.parent.mkdir(parents=True,exist_ok=True); dst.write_bytes(b'conflict')
    try: m.move_file(c,row)
    except RuntimeError: pass
    else: raise AssertionError('Must refuse to overwrite destination')
    assert dst.read_bytes()==b'conflict' and Path(row['current_path']).exists(); dst.unlink()
    m.apply(c,2); m.verify(c); m.rollback(c)
    for p,b in originals.items(): assert p.read_bytes()==b
    m.apply(c); m.verify(c)
    # Simulate a crash after rename but before committing the new catalog path.
    row=c.execute('SELECT * FROM files LIMIT 1').fetchone(); src=Path(row['current_path']); dst=Path(row['original_path'])
    c.execute('INSERT INTO moves(file_id,batch_id,src,dst,sha256,state,created_at) VALUES(?,?,?,?,?,?,?)',(row['id'],row['batch_id'],str(src),str(dst),row['sha256'],'准备恢复',m.NOW())); c.commit(); src.rename(dst)
    m.recover_pending(c); assert c.execute('SELECT current_path FROM files WHERE id=?',(row['id'],)).fetchone()[0]==str(dst)
    m.rollback(c)
    for p,b in originals.items(): assert p.read_bytes()==b
    m.scan(c); assert c.execute('SELECT count(*) FROM files').fetchone()[0]==3
    m.apply(c)
    # Re-downloading an old source path must create a new occurrence, not be silently skipped.
    p=next(iter(originals)); p.write_bytes(originals[p]); m.scan(c); m.plan(c); m.apply(c); m.verify(c)
    assert c.execute('SELECT count(*) FROM files').fetchone()[0]==4
    fid=c.execute('SELECT id FROM files WHERE work_id IS NOT NULL LIMIT 1').fetchone()[0]
    m.assign_author(c,SimpleNamespace(ids=[fid],creator=None,name='人工修正作者',platform_id=None))
    m.plan(c);m.apply(c);m.verify(c)
    assert '人工修正作者' in c.execute('SELECT current_path FROM files WHERE id=?',(fid,)).fetchone()[0]
    assert c.execute('SELECT original_path FROM files WHERE id=?',(fid,)).fetchone()[0] in [str(p) for p in originals]
    assert excluded.read_bytes()==b'untouched'
    c.close()
print('PASS: no-overwrite, duplicate quarantine, rollback, crash recovery, repeated scan, reimport, excluded scope')
