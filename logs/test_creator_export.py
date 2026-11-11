from playwright.sync_api import sync_playwright
from pathlib import Path
import json
out=Path(r'E:\抖音企划\logs\creator_export_download.xlsx')
if out.exists(): out.unlink()
with sync_playwright() as p:
    b=p.chromium.launch(headless=True, executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
    page=b.new_page(accept_downloads=True)
    page.goto('http://127.0.0.1:8766/?test=creator-export', wait_until='networkidle', timeout=30000)
    page.fill('#adminPw','123456')
    page.click('button:has-text("进入管理模式")')
    page.wait_for_timeout(600)
    assert page.locator('button:has-text("导出全部博主 Excel")').count()==1
    with page.expect_download(timeout=30000) as info:
        page.click('button:has-text("导出全部博主 Excel")')
    dl=info.value
    dl.save_as(str(out))
    page.wait_for_timeout(500)
    result={'button':True,'suggested_filename':dl.suggested_filename,'message':page.locator('#msg').inner_text(),'size':out.stat().st_size}
    print(json.dumps(result,ensure_ascii=False))
    b.close()
