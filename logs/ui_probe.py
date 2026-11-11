from playwright.sync_api import sync_playwright
with sync_playwright() as p:
 b=p.chromium.launch(headless=True, executable_path=r"C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe")
 page=b.new_page()
 errs=[]; logs=[]
 page.on('pageerror',lambda e: errs.append(str(e)))
 page.on('console',lambda m: logs.append(m.type+':'+m.text))
 page.goto('http://127.0.0.1:8766/',wait_until='networkidle',timeout=30000)
 print('title',page.title(),'buttons',page.locator('button').count(),'adminPw',page.locator('#adminPw').count())
 page.fill('#adminPw','123456'); page.click('button:has-text("进入管理模式")'); page.wait_for_timeout(1000)
 print('auth',page.locator('#authmsg').inner_text())
 print('errors',errs[:20]); print('logs',logs[:20])
 b.close()

