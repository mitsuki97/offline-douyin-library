"""Check all generated local report links, thumbnails and JavaScript syntax."""
from html.parser import HTMLParser
from pathlib import Path
import json,subprocess,tempfile,urllib.parse,urllib.request
import library as m

class Page(HTMLParser):
    def __init__(self):super().__init__();self.links=[];self.scripts=[];self.in_script=False
    def handle_starttag(self,tag,attrs):
        for k,v in attrs:
            if k in ('href','src') and v:self.links.append(v)
        if tag=='script':self.in_script=True
    def handle_endtag(self,tag):
        if tag=='script':self.in_script=False
    def handle_data(self,data):
        if self.in_script:self.scripts.append(data)

missing=[];links=0;scripts=0;pages=sorted((m.LIB/'报告').glob('*.html'))
for p in pages:
    obj=Page();obj.feed(p.read_text(encoding='utf-8'))
    for url in obj.links:
        parts=urllib.parse.urlsplit(url)
        if parts.scheme=='file':target=Path(urllib.request.url2pathname(parts.path))
        elif not parts.scheme:target=p.parent/urllib.parse.unquote(parts.path)
        else:continue
        links+=1
        if not target.exists():missing.append({'page':p.name,'target':str(target)})
    for script in obj.scripts:
        with tempfile.TemporaryDirectory(prefix='video_report_script_') as tmp:
            js=Path(tmp)/'report.js';js.write_text(script,encoding='utf-8')
            pr=subprocess.run(['C:/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe','--check',str(js)],capture_output=True,text=True)
            assert pr.returncode==0,pr.stderr
        scripts+=1
assert not missing,missing
audit=json.loads((m.LIB/'报告'/'验收核对.json').read_text(encoding='utf-8'))
assert not audit['content_failures'] and not audit['unindexed_source_files']
assert audit['sqlite_integrity']=='ok' and not audit['foreign_key_failures']
excluded=[p for root in m.EXCLUDED if root.exists() for p in root.rglob('*') if p.is_file()]
result=dict(checked_at=m.NOW(),html_pages=len(pages),local_links_and_images=links,missing_links=missing,javascript_syntax_checks=scripts,excluded_files=len(excluded),excluded_bytes=sum(p.stat().st_size for p in excluded),content_audit='通过，详见验收核对.json')
m.write_json(m.LIB/'报告'/'报告检查.json',result)
print(json.dumps(result,ensure_ascii=False,indent=2))
