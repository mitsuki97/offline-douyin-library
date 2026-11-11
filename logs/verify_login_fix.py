from playwright.sync_api import sync_playwright
import json,time
with sync_playwright() as p:
 b=p.chromium.launch(headless=True,executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
 page=b.new_page(); page.goto('http://127.0.0.1:8766/?fix=20261111',wait_until='domcontentloaded',timeout=30000)
 page.click('button:has-text("打开抖音扫码登录")'); page.wait_for_timeout(1500); open_msg=page.locator('#loginmsg').inner_text()
 page.click('button:has-text("检查登录状态")'); page.wait_for_timeout(5000); status_msg=page.locator('#loginmsg').inner_text()
 print(json.dumps({'open_message':open_msg,'status_message':status_msg},ensure_ascii=False))
 b.close()
