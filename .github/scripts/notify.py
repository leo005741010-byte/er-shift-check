#!/usr/bin/env python3
"""急診點班：上班超過 1 小時還有單子沒點 → 推播給那一班有上班的人（GitHub Actions 每 5 分鐘跑一次）

做法：用無頭瀏覽器打開線上的管理頁（跟管理頁「上班超過 1 小時還沒點」同一套判斷，pendingNow），
      班表那一班有誰（onDuty），再用 Web Push 送給那些人開過通知的手機（雲端 push/dev/<裝置>）。
      同一張單同一班只通知一次（push/sent/<日期>）；手機已經取消訂閱的（404/410）從 push/dev 刪掉。
      瀏覽器裡的頁面只讀不寫（攔掉所有寫入雲端的請求），寫入都由這支程式自己做。

環境變數：FB_REFRESH_TOKEN（排程專用的 Firebase 匿名帳號）、VAPID_PRIVATE_KEY（推播私鑰 PEM）
          DRY=1 只試算不送出不寫入；TEST_NAME=名字 只送一則測試通知給那個人；BROWSER_CHANNEL（本機測試用 msedge）
"""
import json, os, sys, tempfile, time, urllib.error, urllib.parse, urllib.request

SITE = os.environ.get('SITE', 'https://leo005741010-byte.github.io/er-shift-check/')
FB = 'https://tenchener-488d2-default-rtdb.firebaseio.com'
NODE = 'erShiftCheckData'
API_KEY = 'AIzaSyB6w2D8gaqSBHoDmtWsWGWGY-DQtDEcyFY'   # Firebase Web API key：本來就公開
RT = os.environ.get('FB_REFRESH_TOKEN', '').strip()
PEM = os.environ.get('VAPID_PRIVATE_KEY', '').strip()
DRY = os.environ.get('DRY', '').strip() == '1'
TEST = os.environ.get('TEST_NAME', '').strip()
CHANNEL = os.environ.get('BROWSER_CHANNEL', 'chrome')
VAPID_SUB = 'https://leo005741010-byte.github.io'   # py_vapid：https 網址只能有網域，不能帶路徑


def log(*a):
    print(*a, flush=True)


def http(method, url, body=None, headers=None):
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode())
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=25) as r:
        t = r.read()
        return json.loads(t) if t else None


_tok = None


def token():
    global _tok
    if not _tok:
        d = http('POST', f'https://securetoken.googleapis.com/v1/token?key={API_KEY}',
                 ('grant_type=refresh_token&refresh_token=' + urllib.parse.quote(RT)).encode(),
                 {'Content-Type': 'application/x-www-form-urlencoded'})
        _tok = d['id_token']
    return _tok


def fb(method, path, body=None):
    p = '/'.join(urllib.parse.quote(x, safe='') for x in f'{NODE}/{path}'.split('/'))
    return http(method, f'{FB}/{p}.json?auth={urllib.parse.quote(token())}', body)


def enc_key(k):   # Firebase 的 key 不能有 . # $ [ ] /
    return ''.join('%%%02X' % ord(c) if c in '.#$[]/%' else c for c in k)


JS = r"""async()=>{
  const t=ymd(new Date()), y=addDays(t,-1);
  const ok=await Promise.all([pull('cfg'),pull(`rec/${mkey(t)}/${dkey(t)}`),pull(`rec/${mkey(y)}/${dkey(y)}`),pull(`tk/${mkey(t)}/${dkey(t)}`),pull(`tk/${mkey(y)}/${dkey(y)}`)]);
  await loadSched([t,y]);
  if(ok.some(x=>!x)) return {err:'讀不到雲端：'+cloud.st};
  applyCfg();
  const g={};
  pendingNow().forEach(x=>{ const k=x.d+'|'+x.s; if(!g[k]) g[k]={d:x.d,s:x.s,shift:SHIFT[x.s].name,names:onDuty(x.d,x.s),items:[]};
    g[k].items.push({key:x.key,f:x.f.name,p:x.p||''}); });
  return {now:new Date().toString(),ver:VERSION,groups:Object.values(g)};
}"""


def pending():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            br = p.chromium.launch(channel=CHANNEL, headless=True)
        except Exception as e:
            log('找不到', CHANNEL, '，改用 Playwright 內建的 Chromium：', e)
            os.system(f'"{sys.executable}" -m playwright install chromium')
            br = p.chromium.launch(headless=True)
        ctx = br.new_context(timezone_id='Asia/Taipei', locale='zh-TW', service_workers='block')
        ctx.add_init_script('try{ localStorage.setItem("erCheck_fbrt",' + json.dumps(json.dumps(RT)) + '); }catch(e){}')

        def guard(route):   # 頁面只讀：寫入雲端的請求一律擋掉
            if route.request.method != 'GET' and 'firebaseio.com' in route.request.url:
                return route.abort()
            return route.continue_()
        ctx.route('**/*', guard)
        pg = ctx.new_page()
        pg.goto(SITE + 'manage.html', wait_until='domcontentloaded', timeout=60000)
        pg.wait_for_function("typeof pendingNow==='function'&&typeof onDuty==='function'&&typeof loadSched==='function'&&typeof applyCfg==='function'", timeout=60000)
        out = pg.evaluate(JS)
        br.close()
        return out


def send(sub, payload, pem_path):
    from pywebpush import webpush, WebPushException
    try:
        webpush(subscription_info=sub, data=json.dumps(payload, ensure_ascii=False), vapid_private_key=pem_path,
                vapid_claims={'sub': VAPID_SUB}, ttl=3 * 3600, headers={'Urgency': 'high'}, timeout=20)
        return 'ok'
    except WebPushException as e:
        code = getattr(e.response, 'status_code', None)
        return 'gone' if code in (404, 410) else f'err {code}: {e}'


def main():
    if not RT:
        sys.exit('沒有 FB_REFRESH_TOKEN')
    if not DRY and not PEM:
        sys.exit('沒有 VAPID_PRIVATE_KEY')
    pem_path = None
    if PEM:
        f = tempfile.NamedTemporaryFile('w', suffix='.pem', delete=False)
        f.write(PEM.replace('\\n', '\n') + '\n')
        f.close()
        pem_path = f.name
    subs = fb('GET', 'push/dev') or {}
    log('開了通知的裝置：', len(subs))

    def targets(names):
        S = set(n.strip() for n in names if n)
        return [(dev, x) for dev, x in subs.items() if isinstance(x, dict) and x.get('sub') and str(x.get('n', '')).strip() in S]

    def push_to(T, payload):
        ok = 0
        for dev, x in T:
            r = 'dry' if DRY else send(x['sub'], payload, pem_path)
            log('  →', x.get('n'), dev, r)
            if r == 'ok':
                ok += 1
            elif r == 'gone':
                fb('DELETE', f'push/dev/{dev}')
        return ok

    if TEST:   # 測試通知
        T = targets([TEST])
        if not T:
            log('「%s」沒有開通知的裝置' % TEST)
            return
        push_to(T, {'title': '急診點班（測試）', 'body': '這是測試通知：之後上班超過 1 小時還有單子沒點，會這樣通知你', 'tag': 'test', 'url': './index.html'})
        return

    r = pending()
    if r.get('err'):
        sys.exit(r['err'])
    log('現在', r['now'], '線上版本 v' + str(r['ver']), '超過 1 小時還沒點：', sum(len(g['items']) for g in r['groups']), '張')
    for g in r['groups']:
        sent = fb('GET', f"push/sent/{g['d']}") or {}
        new = [i for i in g['items'] if enc_key(i['key']) not in sent]
        log(f"{g['d']} {g['shift']}：沒點 {len(g['items'])} 張（還沒通知過 {len(new)} 張）；這一班有 {len(g['names'])} 人：{'、'.join(g['names'])}")
        if not new:
            continue
        T = targets(g['names'])
        if not T:
            log('  這一班沒有人開通知')
            continue
        L = [i['f'] + (f"（{i['p']}）" if i['p'] else '') for i in g['items']]
        payload = {'title': f"急診點班：{g['shift']}還有 {len(L)} 張沒點",
                   'body': '上班超過 1 小時還沒點：' + '、'.join(L[:8]) + (f' 等 {len(L)} 張' if len(L) > 8 else ''),
                   'tag': f"ov-{g['d']}-{g['s']}", 'url': './index.html'}
        n = push_to(T, payload)
        if n and not DRY:
            now = int(time.time() * 1000)
            fb('PATCH', f"push/sent/{g['d']}", {enc_key(i['key']): {'at': now, 'n': n} for i in new})
    # 一週以前的通知紀錄清掉
    if not DRY:
        old = fb('GET', 'push/sent') or {}
        cut = time.strftime('%Y-%m-%d', time.gmtime(time.time() + 8 * 3600 - 7 * 86400))
        for d in old:
            if d < cut:
                fb('DELETE', f'push/sent/{d}')


if __name__ == '__main__':
    main()
