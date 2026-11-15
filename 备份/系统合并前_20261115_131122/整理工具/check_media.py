"""Verify media sampling against a recompressed copy and an unrelated generated clip."""
import tempfile,subprocess,sqlite3
from pathlib import Path
import library as m

with tempfile.TemporaryDirectory(prefix='local_video_media_') as tmp:
    m.BASE=Path(tmp);m.SOURCE=m.BASE/'下载的视频';m.LIB=m.BASE/'本地视频库';m.ALLOWED=[m.SOURCE/'抖音下载',m.SOURCE/'抖音下载2'];m.EXCLUDED=m.SOURCE/'快抖下载器-相对较老';m.DB=m.LIB/'索引'/'library.sqlite3'
    folder=m.ALLOWED[1]/'测试作者';folder.mkdir(parents=True)
    def ff(args):
        p=subprocess.run([str(m.FFMPEG),'-hide_banner','-loglevel','error','-nostdin']+args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
        assert p.returncode==0,p.stderr.decode('utf-8','replace')
    ff(['-f','lavfi','-i','testsrc2=size=128x192:rate=12:duration=8','-c:v','libx264','-g','12','-threads','1',str(folder/'原片.mp4')])
    ff(['-i',str(folder/'原片.mp4'),'-vf','drawbox=x=2:y=2:w=12:h=8:color=white:t=fill','-c:v','libx264','-crf','30','-g','12','-threads','1',str(folder/'压制水印版.mp4')])
    ff(['-f','lavfi','-i','testsrc=size=128x192:rate=12:duration=8','-pix_fmt','yuv420p','-c:v','libx264','-g','12','-threads','1',str(folder/'其他作品.mp4')])
    c=m.connect();m.scan(c);m.media(c);m.plan(c);m.similarities(c)
    rows=list(c.execute('SELECT a.original_path,b.original_path FROM similar s JOIN files a ON a.id=s.a JOIN files b ON b.id=s.b'))
    assert any({Path(a).name,Path(b).name}=={'原片.mp4','压制水印版.mp4'} for a,b in rows),rows
    assert not any('其他作品' in a or '其他作品' in b for a,b in rows),rows
    assert c.execute("SELECT count(*) FROM media WHERE status='抽样可读取'").fetchone()[0]==3
    m.report(c);assert (m.LIB/'报告'/'相似视频对照.html').is_file();c.close()
print('PASS: recompression/watermark candidate found; unrelated moving clip excluded; thumbnails and report generated')
