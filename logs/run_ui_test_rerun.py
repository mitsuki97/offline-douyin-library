from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from pathlib import Path
import json,time
out=Path(r'E:\抖音企划\logs\ui_test_rerun_20261111'); out.mkdir(parents=True,exist_ok=True)
report={'steps':[],'console_errors':[],'page_errors':[],'http_errors':[],'screenshots':[]}
with sync_playwright() as p:
 b=p.chromium.launch(headless=True,executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
 page=b.new_page(viewport={'width':1440,'height':1000})
 page.on('console',lambda m: report['console_errors'].append({'type':m.type,'text':m.text}) if m.type=='error' else None)
 page.on('pageerror',lambda e: report['page_errors'].append(str(e)))
 page.on('response',lambda r: report['http_errors'].append({'status':r.status,'url':r.url}) if r.status>=400 else None)
 page.goto('http://127.0.0.1:8766/?test=rerun20261111',wait_until='networkidle',timeout=30000)
 report['title']=page.title(); report['button_count']=page.locator('button').count(); report['buttons']=page.locator('button').all_inner_texts(); report['input_count']=page.locator('input').count(); report['select_count']=page.locator('select').count()
 page.screenshot(path=str(out/'01_initial.png'),full_page=True); report['screenshots'].append(str(out/'01_initial.png'))
 # management login
 page.fill('#adminPw','123456'); page.click('button:has-text("进入管理模式")'); page.wait_for_timeout(800); report['steps'].append({'name':'管理模式登录','message':page.locator('#authmsg').inner_text()})
 # login status, bounded
 page.click('button:has-text("检查登录状态")'); page.wait_for_timeout(6000); report['steps'].append({'name':'检查登录状态','message':page.locator('#loginmsg').inner_text()})
 if '已检测到登录状态' not in page.locator('#loginmsg').inner_text():
  page.screenshot(path=str(out/'02_login_failed.png'),full_page=True); report['screenshots'].append(str(out/'02_login_failed.png')); report['stop_reason']='登录态未确认';
 else:
  # open login button (ordinary Edge window request), don't rescan/relogin
  page.click('button:has-text("打开抖音扫码登录")'); page.wait_for_timeout(1800); report['steps'].append({'name':'打开普通Edge抖音网页','message':page.locator('#loginmsg').inner_text()})
  # read follows
  page.click('button:has-text("读取我的关注")');
  try:
   page.wait_for_function("document.querySelectorAll('#followList .item').length > 0",timeout=180000)
   page.wait_for_timeout(2500)
   count=page.locator('#followList .item').count(); report['follow_count']=count; report['steps'].append({'name':'读取我的关注','count':count,'message':page.locator('#loginmsg').inner_text()})
   page.screenshot(path=str(out/'03_follow_list.png'),full_page=True); report['screenshots'].append(str(out/'03_follow_list.png'))
   if count<5: report['stop_reason']='关注列表少于5位，停止下载链路'
   else:
    # Select first five only. We do not invoke bulk download yet until link discovery succeeds.
    boxes=page.locator('.relbox');
    for i in range(5): boxes.nth(i).check()
    report['selected_first5']=5
    # Confirm first five names
    report['selected_names']=[page.locator('#followList .item').nth(i).inner_text() for i in range(5)]
    # Trigger save+download; this is the requested core chain, bounded.
    page.click('button:has-text("保存选中并批量下载")'); page.wait_for_timeout(15000)
    report['download_message']=page.locator('#loginmsg').inner_text()
    page.screenshot(path=str(out/'04_after_batch_download.png'),full_page=True); report['screenshots'].append(str(out/'04_after_batch_download.png'))
  except PWTimeout:
   report['stop_reason']='读取关注列表超时'; report['follow_count']=page.locator('#followList .item').count(); page.screenshot(path=str(out/'03_follow_timeout.png'),full_page=True); report['screenshots'].append(str(out/'03_follow_timeout.png'))
 b.close()
(out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
