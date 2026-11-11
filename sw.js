// 急診點班 Service Worker
// 改版後手機還看到舊畫面：把 CACHE 版本號 +1 再推
const CACHE='er-shift-check-v61';
const CORE=['./','./index.html','./manage.html','./supplies.html','./manifest.json','./icon-192.png','./icon-512.png','./apple-touch-icon.png'];

self.addEventListener('install', e=>{
  self.skipWaiting();
  e.waitUntil(caches.open(CACHE).then(c=>c.addAll(CORE).catch(()=>{})));
});

self.addEventListener('activate', e=>{
  e.waitUntil(
    caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k))))
      .then(()=>self.clients.claim())
  );
});

self.addEventListener('fetch', e=>{
  const req=e.request;
  if(req.method!=='GET') return;
  const url=new URL(req.url);
  if(url.origin!==location.origin) return; // Firebase 等跨網域請求直通，不快取

  // HTML：network-first（優先拿最新版），離線退回快取。no-store：GitHub Pages 的 HTML 有 10 分鐘快取，不繞過的話改版後還是舊畫面
  // （manage.html 是用 fetch 去抓 index.html 的，不算頁面導覽，所以 .html 結尾的也要算進來）
  if(req.mode==='navigate' || (req.headers.get('accept')||'').includes('text/html') || /\.html$/.test(url.pathname)){
    e.respondWith(
      fetch(req.url,{cache:'no-store'}).then(res=>{
        const copy=res.clone();
        caches.open(CACHE).then(c=>c.put(req,copy));
        return res;
      }).catch(()=>caches.match(req).then(r=>r||caches.match('./index.html')))
    );
    return;
  }

  // 其他靜態資源（圖示）：cache-first
  e.respondWith(
    caches.match(req).then(cached=>cached || fetch(req).then(res=>{
      if(res.ok){ const copy=res.clone(); caches.open(CACHE).then(c=>c.put(req,copy)); }
      return res;
    }).catch(()=>cached))
  );
});

// 推播（排程送來的「上班超過 1 小時還有單子沒點」）：{title,body,tag,url}
self.addEventListener('push', e=>{
  let d={}; try{ d=e.data?e.data.json():{}; }catch(x){ d={body:e.data?e.data.text():''}; }
  e.waitUntil(self.registration.showNotification(d.title||'急診點班',{body:d.body||'',tag:d.tag||'ershift',renotify:true,icon:'icon-192.png',badge:'icon-192.png',data:{url:d.url||'./index.html'}}));
});
// 點通知：推播＝打開點班；管理頁自己跳的（延遲點班等，沒有 url）＝管理頁已經開著就切回去，否則打開管理頁
self.addEventListener('notificationclick', e=>{
  e.notification.close();
  const u=(e.notification.data&&e.notification.data.url)||'./manage.html', mg=/manage\.html/.test(u);
  e.waitUntil(self.clients.matchAll({type:'window',includeUncontrolled:true}).then(cs=>{
    for(const c of cs){ if((mg?/manage\.html/.test(c.url):!/manage\.html|supplies\.html/.test(c.url))&&'focus' in c) return c.focus(); }
    return self.clients.openWindow(u);
  }));
});
