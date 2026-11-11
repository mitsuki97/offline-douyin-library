from playwright.sync_api import sync_playwright
import json
with sync_playwright() as p:
 b=p.chromium.connect_over_cdp('http://127.0.0.1:9227')
 c=b.contexts[0]; page=c.pages[0]
 print('URL',page.url)
 rows=page.locator('a[href*="/user/"]').evaluate_all("els=>els.map((a,i)=>({i,text:(a.innerText||'').trim(),href:a.href,cls:a.className,aria:a.getAttribute('aria-label'),parent:(a.parentElement?.innerText||'').trim().slice(0,250)}))")
 print(json.dumps(rows,ensure_ascii=False,indent=2))
