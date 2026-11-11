from playwright.sync_api import sync_playwright
import json,re,time
url='https://www.douyin.com/user/MS4wLjABAAAAEZVA25vIp5-WYMhIe3BnBtueZOO7yq_3lnNPGx8eWLEDCFd-_TT3k93TZyQQsLqK'
found=[]
def walk(x):
 if isinstance(x,dict):
  for k in ('aweme_list','awemeList'):
   if isinstance(x.get(k),list):
    for a in x[k]:
     if isinstance(a,dict) and a.get('aweme_id'): found.append(a)
  for k,v in x.items():
   if k not in ('aweme_list','awemeList'): walk(v)
 elif isinstance(x,list):
  for v in x: walk(v)
with sync_playwright() as p:
 b=p.chromium.connect_over_cdp('http://127.0.0.1:9227'); c=b.contexts[0]; page=c.pages[0]
 def on_response(r):
  if any(x in r.url.lower() for x in ('aweme','post','work','item_list')):
   try: walk(r.json())
   except: pass
 page.on('response',on_response); page.goto(url,wait_until='domcontentloaded',timeout=30000); page.wait_for_timeout(7000)
 for _ in range(5): page.mouse.wheel(0,1200); page.wait_for_timeout(1200)
 uniq={str(a.get('aweme_id')):a for a in found}
 out=[]
 for a in list(uniq.values())[:3]: out.append({'id':a.get('aweme_id'),'desc':a.get('desc'),'video':a.get('video'),'statistics':a.get('statistics')})
 print(json.dumps(out,ensure_ascii=False,indent=2)[:20000])
