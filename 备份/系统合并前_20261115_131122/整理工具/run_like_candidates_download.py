import json, os, re, time, csv
from pathlib import Path
from yt_dlp import YoutubeDL
manifest=Path(r'E:\抖音企划\outputs\01a19ce1-1e70-7e70-bcb4-5755707e4f14\like_candidates_download.json')
data=json.loads(manifest.read_text(encoding='utf-8'))
outroot=Path(r'E:\抖音企划\本地视频库\按博主分类'); outroot.mkdir(parents=True,exist_ok=True)
logdir=Path(r'E:\抖音企划\本地视频库\下载任务'); logdir.mkdir(parents=True,exist_ok=True)
runlog=logdir/f'like_candidates_{time.strftime("%Y%m%d_%H%M%S")}.csv'
rows=[]
by={}
for x in data: by.setdefault(x['博主'] or '未知博主',[]).append(x)
common={'quiet':False,'noprogress':False,'ignoreerrors':True,'retries':2,'fragment_retries':2,'continuedl':True,'writethumbnail':False,'writeinfojson':True,'noplaylist':True,'cookiefile':r'E:\抖音企划\本地视频库\下载器\douyin_login_cookies.txt','http_headers':{'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0','Referer':'https://www.douyin.com/','Accept-Language':'zh-CN,zh;q=0.9,en;q=0.8'}}
for creator,items in sorted(by.items()):
 folder=outroot/creator; folder.mkdir(parents=True,exist_ok=True)
 archive=folder/'.yt-dlp-archive.txt'
 opts={**common,'outtmpl':str(folder/'%(upload_date)s_%(id)s_%(title).120s.%(ext)s'),'download_archive':str(archive)}
 urls=[x['作品链接'] for x in items]
 print(f'[{creator}] {len(urls)} 条')
 try:
  with YoutubeDL(opts) as ydl:
   ret=ydl.download(urls)
  status='完成'
 except Exception as e:
  status='失败'; print(f'[{creator}] ERROR {e}')
 for x in items: rows.append([creator,x['作品ID'],x['作品链接'],status])
with runlog.open('w',encoding='utf-8-sig',newline='') as f:
 w=csv.writer(f); w.writerow(['博主','作品ID','作品链接','批次状态']); w.writerows(rows)
print('总数',len(data),'博主数',len(by),'日志',runlog)
