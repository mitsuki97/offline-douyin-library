import sqlite3, json, html, csv
from pathlib import Path

BASE=Path(r'E:\抖音企划')
LIB=BASE/'本地视频库'
DB=LIB/'索引'/'library.sqlite3'
REPORT=LIB/'报告'
OPS=LIB/'操作记录'

def esc(x): return html.escape(str(x or ''))

con=sqlite3.connect(DB)
con.row_factory=sqlite3.Row
creator_rows=[dict(r) for r in con.execute('''
select c.id,c.name,c.platform_id,c.identity_status,
 count(f.id) file_count,
 sum(case when f.kind='video' then 1 else 0 end) video_count,
 sum(case when f.kind='image' then 1 else 0 end) image_count,
 sum(case when f.duplicate_of is not null then 1 else 0 end) duplicate_count,
 sum(case when f.creator_id is null then 1 else 0 end) unknown_count
from creators c left join files f on f.creator_id=c.id
group by c.id order by file_count desc,c.name,c.id''')]
unknown=dict(con.execute('''select count(*) file_count,
 sum(case when kind='video' then 1 else 0 end) video_count,
 sum(case when kind='image' then 1 else 0 end) image_count,
 sum(case when duplicate_of is not null then 1 else 0 end) duplicate_count
from files where creator_id is null and status!='已永久删除' ''').fetchone())
same={}
for r in creator_rows: same.setdefault(r['name'],[]).append(r)
same_names={k:v for k,v in same.items() if len(v)>1}
status_rows=[dict(r) for r in con.execute('''select f.id,f.current_path,f.original_path,f.extension,f.kind,f.status,
 m.status media_status,m.detail,w.title,coalesce(c.name,'博主待确认') creator
 from files f left join media m on m.file_id=f.id left join works w on w.id=f.work_id
 left join creators c on c.id=f.creator_id
 where f.status!='已永久删除' and m.status in ('下载未完成','图片异常','无法抽帧','检查失败','解码有告警','仅音频，扩展名为MP4')
 order by m.status,f.id''')]
OPS.mkdir(parents=True,exist_ok=True)
with (OPS/'异常文件候选.csv').open('w',encoding='utf-8-sig',newline='') as f:
    fields=['id','media_status','creator','title','current_path','original_path','detail']
    w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows({k:r.get(k) for k in fields} for r in status_rows)

data=json.dumps(creator_rows,ensure_ascii=False).replace('<','\\u003c')
same_data=json.dumps({k:[x['id'] for x in v] for k,v in same_names.items()},ensure_ascii=False).replace('<','\\u003c')
status_data=json.dumps(status_rows,ensure_ascii=False).replace('<','\\u003c')
max_count=max([r['file_count'] or 0 for r in creator_rows]+[1])
cards=[]
for r in creator_rows:
    same_flag=' 同名身份待核对' if len(same.get(r['name'],[]))>1 else ''
    cards.append(f'''<article class="creator" data-name="{esc(r['name']).lower()}" data-id="{esc(r['id']).lower()}" data-status="{esc(r['identity_status']).lower()}">
    <div class="bar" style="width:{(r['file_count'] or 0)/max_count*100:.1f}%"></div>
    <h3>{esc(r['name'])}<span class="flag">{esc(same_flag)}</span></h3>
    <p>文件 {r['file_count'] or 0} · 视频 {r['video_count'] or 0} · 图片 {r['image_count'] or 0} · 重复副本 {r['duplicate_count'] or 0}</p>
    <small>博主编号：{esc(r['id'])}<br>身份状态：{esc(r['identity_status'])}<br>平台标识：{esc(r['platform_id'])}</small></article>''')
html_text='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>博主分类可视化</title>
<style>body{font:16px/1.6 "Microsoft YaHei",sans-serif;background:#f4f6fb;color:#202535;max-width:1200px;margin:24px auto;padding:0 20px}h1{font-size:28px}.summary{display:flex;gap:12px;flex-wrap:wrap}.summary div{background:#fff;border:1px solid #dfe4ef;border-radius:10px;padding:12px 16px}.toolbar{position:sticky;top:0;background:#f4f6fb;padding:12px 0;z-index:2}input,select{font:inherit;padding:9px;border:1px solid #bbc5d8;border-radius:7px;margin-right:8px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px}.creator{position:relative;background:#fff;border:1px solid #dfe4ef;border-radius:10px;padding:14px;overflow:hidden}.bar{height:5px;background:#4c73d9;border-radius:4px;margin:-14px -14px 10px}.creator h3{margin:5px 0;font-size:18px}.creator p{margin:4px 0}.creator small{color:#596579;overflow-wrap:anywhere}.flag{font-size:12px;color:#b45100;margin-left:8px}.note{background:#fff6df;border:1px solid #f0d58c;border-radius:8px;padding:12px;margin:14px 0}a{color:#244ea8}</style>
<h1>博主分类可视化</h1><div class="summary"><div>博主身份记录：<b>'''+str(len(creator_rows))+'''</b></div><div>待确认文件：<b>'''+str(unknown['file_count'] or 0)+'''</b></div><div>同名身份组：<b>'''+str(len(same_names))+'''</b></div><div>异常候选：<b>'''+str(len(status_rows))+'''</b></div></div>
<div class="note">同名博主按不同编号分别展示，不自动合并。先用搜索和数量筛选定位，再根据博主编号人工确认。</div>
<div class="toolbar"><input id="q" placeholder="搜索博主名或编号" oninput="filterCards()"><select id="s" onchange="filterCards()"><option value="">全部身份状态</option><option value="待核实身份">待核实身份</option><option value="人工确认">人工确认</option></select> <a href="博主清单.csv">打开博主清单 CSV</a>　<a href="../操作记录/异常文件候选.csv">打开异常文件候选 CSV</a></div><div class="grid">'''+''.join(cards)+'''</div>
<script>function filterCards(){let q=document.getElementById('q').value.toLowerCase(),s=document.getElementById('s').value;document.querySelectorAll('.creator').forEach(x=>x.hidden=!(x.dataset.name.includes(q)||x.dataset.id.includes(q))||!!s&&!x.dataset.status.includes(s.toLowerCase()))}</script></html>'''
(REPORT/'博主分类可视化.html').write_text(html_text,encoding='utf-8')
print(json.dumps({'creator_count':len(creator_rows),'same_name_groups':len(same_names),'unknown_files':unknown['file_count'] or 0,'exception_candidates':len(status_rows),'visual':str(REPORT/'博主分类可视化.html'),'exception_csv':str(OPS/'异常文件候选.csv')},ensure_ascii=False,indent=2))
