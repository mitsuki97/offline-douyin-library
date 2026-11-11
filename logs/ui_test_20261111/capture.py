from playwright.sync_api import sync_playwright
from pathlib import Path
import json,time
out=Path(r'E:\抖音企划\logs\ui_test_20261111')
with sync_playwright() as p:
 b=p.chromium.launch(headless=True,executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
 page=b.new_page(viewport={'width':1440,'height':1000}); events=[]; errors=[]
 page.on('console',lambda m: events.append({'type':m.type,'text':m.text}))
 page.on('pageerror',lambda e: errors.append(str(e)))
 page.goto('http://127.0.0.1:8766/?test=report',wait_until='domcontentloaded',timeout=30000); page.wait_for_timeout(1000)
 page.screenshot(path=str(out/'page_initial.png'),full_page=True)
 page.fill('#adminPw','123456'); page.click('button:has-text("进入管理模式")'); page.wait_for_timeout(500)
 page.click('button:has-text("检查登录状态")'); page.wait_for_timeout(7000)
 page.screenshot(path=str(out/'login_status_timeout.png'),full_page=True)
 print(json.dumps({'buttons':page.locator('button').all_inner_texts(),'inputs':page.locator('input').count(),'selects':page.locator('select').count(),'auth':page.locator('#authmsg').inner_text(),'loginmsg':page.locator('#loginmsg').inner_text(),'events':events,'pageerrors':errors},ensure_ascii=False,indent=2))
 b.close()
