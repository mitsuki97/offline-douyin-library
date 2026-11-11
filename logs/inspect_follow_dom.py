from playwright.sync_api import sync_playwright
import json
with sync_playwright() as p:
 b=p.chromium.connect_over_cdp('http://127.0.0.1:9227'); c=b.contexts[0]; page=c.pages[0]
 rows=page.locator('a[href*="/user/"]').evaluate_all("els=>els.map((a,i)=>({i,href:a.href,text:(a.innerText||'').trim(),p1:(a.parentElement?.className||''),p2:(a.parentElement?.parentElement?.className||''),p3:(a.parentElement?.parentElement?.parentElement?.className||''),outer:(a.parentElement?.parentElement?.outerHTML||'').slice(0,700)}))")
 print(json.dumps(rows[8:],ensure_ascii=False,indent=2))
