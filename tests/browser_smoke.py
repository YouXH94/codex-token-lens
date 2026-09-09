"""Development-only browser regression suite. Runtime needs no Playwright.
Browser navigation is blocked by this environment policy, so pages are rendered
with set_content. Live data is exercised through an explicit Python HTTP bridge;
this is not a native-browser cookie / same-origin integration test.
"""
import json
import os
import shutil
from pathlib import Path
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.error
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import lens
from test_lens import meta,ctx,settings,tok,u
from playwright.sync_api import sync_playwright

BASE=Path(__file__).resolve().parents[1]
checks=[];errors=[]
def check(ok,label):
    if not ok:raise AssertionError(label)
    checks.append(label)

def state(page):return page.evaluate('LensTest.getState()')

def run():
    preview=(BASE/'preview.html').read_text(encoding='utf-8')
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.environ.get('LENS_CHROMIUM') or shutil.which('chromium') or None,headless=True,args=['--no-sandbox'] if os.name != 'nt' and hasattr(os, 'geteuid') and os.geteuid() == 0 else [])
        context=browser.new_context(viewport={'width':1512,'height':1080},timezone_id='UTC',accept_downloads=True)
        page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
        requests=[];page.on('request',lambda r:requests.append(r.url))
        page.set_content(preview);page.wait_for_selector('#threadRows tr[data-thread]')
        check('演示数据' in page.locator('#notice').inner_text(),'Synthetic preview is explicitly labelled')
        check(state(page)['ACTIVE']=='threads','Threads is the default view')
        check(page.locator('#detailPanel').is_visible(),'Desktop opens a populated right detail pane')
        check('百万' in page.locator('#kpis').inner_text(),'Headline counters use Chinese units')
        check(page.locator('#threadRows tr[data-thread]').count()==6,'Table is paginated at six rows')
        initial=state(page)['total'];first_id=page.locator('#threadRows tr').first.get_attribute('data-thread')
        page.click('#nextPage');check(page.locator('#threadRows tr').first.get_attribute('data-thread')!=first_id,'Pagination changes visible rows')
        page.click('#prevPage')
        page.locator('[data-sort="total_tokens"]').click()
        cell_values=page.locator('#threadRows tr td:nth-child(2) .number').evaluate_all('(els)=>els.map(e=>Number(e.title.replaceAll(",","")))')
        check(cell_values==sorted(cell_values),'Numeric sorting is based on raw values, not formatted text')
        page.locator('[data-sort="total_tokens"]').click()
        page.fill('#search','NOT-A-REAL-THREAD-94832');page.wait_for_timeout(200)
        check(state(page)['eventCount']==0,'Search produces an explicit empty state')
        page.click('#clearFilters');check(state(page)['total']==initial,'Reset filters restores original total')
        page.select_option('#modelFilter','gpt-6-astra')
        check(0<state(page)['total']<initial,'Model filtering changes the accounting scope')
        page.click('#clearFilters')
        page.locator('#threadRows tr[data-thread]').nth(1).click()
        check(page.locator('#detailPanel').is_visible(),'Clicking a row opens its details')
        page.click('#showExact');check(',' in page.locator('.exact-table').inner_text(),'Exact values are available only on demand')
        page.click('#closeDialog')
        page.click('#renameThread');page.fill('#aliasInput','本地别名 <img src=x onerror=alert(1)>');page.click('#saveAlias')
        check('<img src=x' in page.locator('#detailPanel h2').inner_text(),'User-provided aliases render as escaped text')
        check(page.locator('#detailPanel h2 img').count()==0,'Alias HTML is not interpreted')
        check('本地别名' in page.locator('#threadRows').inner_text(),'Alias updates are reflected in the table without backend writes')
        page.locator('[data-detail-tab="events"]').click()
        check(page.locator('.event-row').count()>0,'Thread event ledger is populated')
        page.locator('[data-detail-tab="usage"]').click()
        with page.expect_download() as dl:page.click('#detailJSON')
        data=json.loads(Path(dl.value.path()).read_text())
        check(all(isinstance(e['total_tokens'],int) for e in data['events']),'Thread JSON exports preserve exact integer counts')
        check(all(not any(k.startswith('_') for k in e) for e in data['events']),'UI-only derived fields are excluded from exports')
        page.click('#mergeChildren');check(state(page)['total']==initial,'Merging subagents preserves the total without double counting')
        merged=state(page)['groupCount'];page.click('#mergeChildren');check(state(page)['groupCount']>=merged,'Independent child rows are recoverable')
        page.locator('[data-tab="daily"]').click()
        check(page.locator('#primaryChart [data-bin]').count()==24,'Daily chart contains 24 hourly buckets')
        day_total=state(page)['total'];page.locator('#primaryChart [data-bin="7"]').click()
        check(state(page)['selectedHour']==7 and state(page)['total']<=day_total,'Clicking an hour filters the related threads')
        page.click('#clearHour');check(state(page)['total']==day_total,'Clearing the hour restores the full selected day')
        page.fill('#dayPicker','2026-09-07');page.locator('#dayPicker').dispatch_event('change')
        check(state(page)['selectedDay']=='2026-09-07','Date picker changes the hourly source date')
        page.click('[data-shift-day="1"]');check(state(page)['selectedDay']=='2026-09-08','Day navigation moves by one date')
        page.locator('[data-tab="cycle"]').click();page.select_option('#cycleType','week')
        per=page.evaluate('LensTest.period()');check(per=={'start':'2026-09-07','end':'2026-09-13'},'Weekly range uses Monday through Sunday')
        page.select_option('#cycleType','custom');page.fill('#customStart','2026-09-08');page.fill('#customEnd','2026-09-01');page.click('#saveCycle')
        check(page.locator('#cycleError').inner_text()!='','Invalid custom date range is rejected')
        page.fill('#customStart','2026-09-03');page.fill('#customEnd','2026-09-08');page.click('#saveCycle')
        check(page.evaluate('LensTest.period()')=={'start':'2026-09-03','end':'2026-09-08'},'Inclusive custom date range is applied')
        page.locator('#primaryChart [data-bin="0"]').click()
        check(state(page)['ACTIVE']=='daily' and state(page)['selectedDay']=='2026-09-03','Cycle bars drill down to the selected date')
        page.locator('#miniHeatmap [data-day="2026-09-07"]').click()
        check(state(page)['selectedDay']=='2026-09-07','Heatmap cells drill down to hourly view')
        page.locator('.navbtn[data-page="settings"]').click()
        page.select_option('#unitSetting','wan');check(page.evaluate('LensTest.n(124532)')=='12.45万','Traditional ten-thousand units can be selected')
        page.select_option('#unitSetting','full');check(page.evaluate('LensTest.n(124532)')=='1.25十万','Explicit ten-thousand / hundred-thousand / million units can be restored')
        page.select_option('#zoneSetting','America/New_York')
        a=page.evaluate('LensTest.dateParts("2026-11-01T05:30:00Z")');b=page.evaluate('LensTest.dateParts("2026-11-01T06:30:00Z")')
        check(a['hour']==b['hour']==1,'Repeated daylight-saving clock hours share the labelled bucket')
        check(page.evaluate('LensTest.dateParts(null).date')=='unknown','Missing timestamps are not converted to January 1970')
        page.select_option('#zoneSetting','UTC')
        page.locator('.navbtn[data-page="diagnostics"]').click()
        check('日志中的额度快照' in page.locator('#diagnosticsPage').inner_text(),'Quota snapshots are separated from token budgets')
        page.locator('.navbtn[data-page="models"]').click()
        page.locator('#modelsPage [data-model]').first.click()
        check(state(page)['SECTION']=='monitor' and state(page)['ACTIVE']=='threads','Model distribution links back to filtered threads')
        page.click('#clearFilters');page.locator('[data-tab="threads"]').click();page.select_option('#cycleType','month');page.click('#currentCycle')
        check(state(page)['total']==initial,'Changing display preferences does not change raw totals')
        page.click('#exportBtn')
        with page.expect_download() as dl:page.click('#exportCSV')
        csv_data=Path(dl.value.path()).read_text(encoding='utf-8-sig')
        check('total_tokens' in csv_data and len(csv_data.splitlines())>2,'CSV export contains exact event rows')
        page.locator('#threadRows tr[data-thread]').first.focus();page.keyboard.press('Enter')
        check(page.locator('#detailPanel').is_visible(),'Keyboard Enter opens a thread detail')
        page.keyboard.press('Escape');check(page.locator('#detailPanel').is_hidden(),'Escape closes the detail pane')
        check(not [url for url in requests if url.startswith(('http:','https:'))],'Standalone HTML preview requests no external assets or APIs')
        page.set_content(preview);page.wait_for_selector('#threadRows tr[data-thread]')
        page.screenshot(path=str(BASE/'tests'/'preview-desktop.png'),full_page=True)
        # Mobile and narrow desktop layout verification.
        mobile=context.new_page();mobile.set_viewport_size({'width':390,'height':844});mobile.on('pageerror',lambda e:errors.append(str(e)))
        mobile.set_content(preview);mobile.wait_for_selector('#threadRows tr[data-thread]')
        check(mobile.evaluate('document.documentElement.scrollWidth<=innerWidth'),'390px viewport has no document-level horizontal overflow')
        check(mobile.locator('#detailPanel').is_hidden(),'Mobile does not auto-open the detail drawer')
        mobile.locator('#threadRows tr[data-thread]').first.click();check(mobile.locator('#detailPanel').is_visible(),'Mobile row opens the bottom-sheet detail')
        mobile.click('#closeDetail');mobile.evaluate("document.querySelectorAll('.table-wrap').forEach(e=>e.scrollLeft=0)");mobile.screenshot(path=str(BASE/'tests'/'preview-mobile.png'),full_page=True)
        mobile.set_viewport_size({'width':768,'height':1024});check(mobile.evaluate('document.documentElement.scrollWidth<=innerWidth'),'768px tablet has no document-level horizontal overflow')
        # HTTP-backed bootstrap through a test bridge. Navigation itself is blocked.
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/'codex';(root/'sessions').mkdir(parents=True)
            log=root/'sessions'/'test.jsonl';log.write_text(''.join(json.dumps(r)+'\n' for r in [meta(),ctx(),settings(),tok(u(),u())]))
            app=lens.App(lens.Store(Path(td)/'db.sqlite3',[{'path':str(root),'label':'Live test'}],'UTC'),1)
            server=lens.ThreadingHTTPServer(('127.0.0.1',0),app.handler());threading.Thread(target=server.serve_forever,daemon=True).start()
            url=f'http://127.0.0.1:{server.server_port}/?key={app.token}'
            ctxlive=browser.new_context(viewport={'width':1440,'height':1000},timezone_id='UTC');live=ctxlive.new_page();live.on('pageerror',lambda e:errors.append(str(e)))
            authorized=[True]; network=[]
            def bridge(path,headers):
                urlpath=f'http://127.0.0.1:{server.server_port}'+path
                network.append(urlpath)
                h=dict(headers)
                if authorized[0]:h['Cookie']='lens_token='+app.token
                try:r=urllib.request.urlopen(urllib.request.Request(urlpath,headers=h),timeout=3)
                except urllib.error.HTTPError as e:r=e
                with r:return {'status':r.code,'body':r.read().decode(),'headers':dict(r.headers)}
            live.expose_function('lensHTTP',bridge)
            runtime_html=lens.render_html().replace('/*__EMBEDDED__*/', '''window.fetch = async function(url, options={}) {
                const r=await window.lensHTTP(String(url),options.headers||{});
                return new Response(r.status===304?null:r.body,{status:r.status,headers:r.headers});
            };''')
            try:
                live.set_content(runtime_html);live.wait_for_selector('#threadRows tr[data-thread]')
                check('本地已连接' in live.locator('#connection').inner_text(),'Runtime frontend bootstraps from actual HTTP data via the test bridge')
                check(live.locator('#notice').is_hidden(),'Live data is not labelled as demonstration data')
                live.locator('[data-group-range="all"]').click();check(state(live)['total']==120,'Live frontend totals match backend counts')
                original=log.read_bytes()
                with log.open('a') as f:f.write(json.dumps(tok(u(200,120,40,20),u(),3))+'\n')
                app.refresh();live.click('#refreshBtn');live.wait_for_function('LensTest.getState().total===240')
                check(state(live)['total']==240,'Appended usage reaches the real HTTP frontend')
                check(all(urlsplit.startswith(f'http://127.0.0.1:{server.server_port}/') for urlsplit in network),'Runtime data bridge calls only the own loopback API')
                # Replace events with an empty response, without touching source logs.
                app.latest={**app.latest,'events':[],'sessions':[]};app.revision+=1
                live.click('#refreshBtn');live.wait_for_function('LensTest.getState().eventCount===0')
                check('此范围暂无用量' in live.locator('#threadRows').inner_text(),'Empty backend data has an explicit usable empty state')
                authorized[0]=False;live.click('#refreshBtn');live.wait_for_function('document.getElementById("connection").textContent.includes("服务未连接")')
                check('授权失效' in live.locator('#notice').inner_text(),'Expired local authorization shows an actionable error')
            finally:app.stop.set();server.shutdown();server.server_close();ctxlive.close()
        browser.close()
    check(not errors,'No JavaScript runtime errors across snapshot, mobile and real HTTP tests')
    result={'passed':len(checks),'checks':checks,'js_errors':errors,'runtime':'Linux Chromium set_content; HTTP-backed frontend bridge. Browser URL navigation is policy-blocked. Not native cookie/same-origin integration or macOS/Windows validation.'}
    (BASE/'tests'/'browser-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':run()
