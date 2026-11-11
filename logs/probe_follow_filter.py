from playwright.sync_api import sync_playwright
import json,sys; sys.stdout.reconfigure(encoding='utf-8',errors='replace')
js=r'''() => Array.from(document.querySelectorAll("a[href*='/user/']")).map(a=>{let e=a, ok=false, texts=[]; for(let i=0;i<7&&e;i++,e=e.parentElement){let t=(e.innerText||'').trim(); texts.push(t.slice(0,120)); if(t.includes('已关注'))ok=true;} return {text:(a.innerText||'').trim(),href:a.href,ok,texts};}).filter(x=>x.text)'''
with sync_playwright() as p:
 b=p.chromium.connect_over_cdp('http://127.0.0.1:9227'); page=b.contexts[0].pages[0]
 out=page.evaluate(js); print(json.dumps(out,ensure_ascii=False,indent=2))
