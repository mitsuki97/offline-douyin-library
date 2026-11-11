from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from pathlib import Path
import json,time
base=Path(r'E:\抖音企划\logs\ui_test_20261111'); base.mkdir(parents=True,exist_ok=True)
report={'url':'http://192.168.18.65:8766/?test=20261111','buttons':[],'console_errors':[],'page_errors':[],'screenshots':[]}
with sync_playwright() as p:
    b=p.chromium.launch(headless=True,executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
    page=b.new_page(viewport={'width':1440,'height':1000})
    page.on('console',lambda m: report['console_errors'].append(m.text) if m.type=='error' else None)
    page.on('pageerror',lambda e: report['page_errors'].append(str(e)))
    page.goto(report['url'],wait_until='networkidle',timeout=30000)
    report['title']=page.title(); report['visible_buttons']=page.locator('button').all_inner_texts(); report['inputs']=page.locator('input').count(); report['selects']=page.locator('select').count()
    # admin login
    page.fill('#adminPw','123456'); page.click('button:has-text("进入管理模式")'); page.wait_for_timeout(700); report['admin_message']=page.locator('#authmsg').inner_text()
    # admin logout
    page.click('button:has-text("退出管理模式")'); page.wait_for_timeout(300); report['logout_message']=page.locator('#authmsg').inner_text()
    # open login; should return immediately
    page.click('button:has-text("打开抖音扫码登录")'); page.wait_for_timeout(1200); report['login_open_message']=page.locator('#loginmsg').inner_text()
    # login status: wait for the UI response, bounded to avoid hanging on active harvest
    page.click('button:has-text("检查登录状态")'); page.wait_for_timeout(6000); report['login_status_message']=page.locator('#loginmsg').inner_text()
    if '失败' in report['login_status_message'] or '超时' in report['login_status_message'] or not report['login_status_message']:
        shot=base/'login_status_blocked.png'; page.screenshot(path=str(shot),full_page=True); report['screenshots'].append(str(shot)); report['stopped_after_login_block']=True
    # inspect current page after the blocked step, no data pulling/download actions
    report['final_url']=page.url
    b.close()
(base/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
