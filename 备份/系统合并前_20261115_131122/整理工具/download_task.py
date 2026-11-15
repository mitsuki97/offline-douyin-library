"""Run one exported creator download task with yt-dlp.

The downloader writes only to the task's待导入 directory. It does not modify the
SQLite catalog; import remains a separate, reviewable step.
"""
from __future__ import annotations
import argparse, json, re, time
from pathlib import Path
from yt_dlp import YoutubeDL

try:
    if hasattr(__import__('sys').stdout, 'reconfigure'):
        __import__('sys').stdout.reconfigure(encoding='utf-8', errors='replace')
        __import__('sys').stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

def main():
    ap=argparse.ArgumentParser(description='Run an offline-Douyin creator download task')
    ap.add_argument('task',type=Path)
    args=ap.parse_args(); task=json.loads(args.task.read_text(encoding='utf-8'))
    url=(task.get('homepage_url') or '').strip()
    if not url: raise SystemExit('任务没有博主主页链接，请先在网页订阅中填写。')
    out=Path(task['download_dir']); out.mkdir(parents=True,exist_ok=True)
    archive=out/'.yt-dlp-archive.txt'
    start=(task.get('start_date') or '').replace('-','')
    opts={
        'outtmpl':str(out/'%(uploader)s/%(upload_date)s_%(id)s_%(title).120s.%(ext)s'),
        'download_archive':str(archive),
        'ignoreerrors':False,
        'writethumbnail':True,
        'writeinfojson':True,
        'noplaylist':False,
        'restrictfilenames':False,
        'retries':3,
        'continuedl':True,
        'quiet':False,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0',
            'Referer': 'https://www.douyin.com/',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        },
    }
    cookie_mode=(task.get('cookie_mode') or '').strip().lower()
    if cookie_mode in ('chrome','edge','firefox','brave','chromium'):
        opts['cookiesfrombrowser']=(cookie_mode,)
    elif cookie_mode=='file' and task.get('cookie_file'):
        cookie_file=Path(task['cookie_file']).expanduser().resolve()
        if not cookie_file.exists(): raise SystemExit(f'Cookie 文件不存在：{cookie_file}')
        opts['cookiefile']=str(cookie_file)
    if start and re.fullmatch(r'\d{8}',start): opts['dateafter']=int(start)
    records=task.get('video_items') or []
    urls=task.get('video_urls') or [url]
    start_date=task.get('start_date') or ''
    if start_date:
        records=[r for r in records if not r.get('publish_time') or str(r.get('publish_time',''))[:10]>=start_date]
    print('开始下载：',url); print('作品数量：',len(records) if records else len(urls)); print('保存目录：',out); print('开始日期：',start_date or '未设置')
    try:
        if records and any(r.get('media_urls') for r in records):
            import urllib.request
            cookie_header=''
            cookie_file=task.get('cookie_file')
            if cookie_file and Path(cookie_file).exists():
                vals=[]
                for line in Path(cookie_file).read_text(encoding='utf-8',errors='ignore').splitlines():
                    if not line or line.startswith('#'): continue
                    parts=line.split('\t')
                    if len(parts)>=7 and parts[6]: vals.append(f'{parts[5]}={parts[6]}')
                cookie_header='; '.join(vals)
            for rec in records:
                aid=str(rec.get('aweme_id') or 'unknown'); title=re.sub(r'[\\/:*?"<>|]+','_',str(rec.get('title') or aid)).strip()[:120]
                # Keep Windows filenames valid while retaining the original title.
                title=re.sub(r'[\x00-\x1f<>:"/\\|?*]+','_',title).strip(' .')[:120]
                target=out/f'{aid}_{title or aid}.mp4'
                if target.exists() and target.stat().st_size>0: print('已存在：',target); continue
                media=rec.get('media_urls') or []
                if not media: print('无媒体地址：',aid); continue
                headers={'User-Agent':opts['http_headers']['User-Agent'],'Referer':'https://www.douyin.com/'}
                if cookie_header: headers['Cookie']=cookie_header
                last=None
                for media_url in media[:3]:
                    try:
                        req=urllib.request.Request(media_url,headers=headers)
                        with urllib.request.urlopen(req,timeout=90) as resp: data=resp.read()
                        if len(data)<1024: raise RuntimeError(f'响应过小：{len(data)} bytes')
                        target.write_bytes(data); print('已下载：',target); last=None; break
                    except Exception as exc: last=exc; time.sleep(1)
                if last: print('下载失败：',aid,last)
        else:
            with YoutubeDL(opts) as ydl: result=ydl.download(urls)
    except Exception as exc:
        raise SystemExit(f'下载失败：yt-dlp 不支持此主页地址或页面需要专用发现流程。原始错误：{exc}')
    print('下载完成。请回到网页执行“导入新下载”，再进行查重和媒体检查。')
if __name__=='__main__': main()
