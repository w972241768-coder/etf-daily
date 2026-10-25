/* ETF 轮动日报 · Service Worker
 *
 * ⚠️ 策略要点：本页是**每日更新的金融数据**，绝不能用 cache-first，
 *    否则「添加到主屏幕」后永远看到第一次缓存的那份持仓，后果比打不开严重。
 *    因此：
 *      · 导航请求（HTML）→ network-first，先取最新，失败才回落缓存；
 *      · 图标等静态资源 → stale-while-revalidate（秒开 + 后台更新）。
 *    版本号变更时清理旧缓存。
 */
const VER = 'etf-daily-v1';
const SHELL = [
  './',
  './icon-192.png',
  './icon-512.png',
  './apple-touch-icon.png',
  './manifest.json',
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(VER)
      .then((c) => c.addAll(SHELL).catch(() => null))  // 任一失败不阻断安装
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((ks) => Promise.all(ks.filter((k) => k !== VER).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;   // 跨域一律直连

  // HTML：网络优先（保证每天看到最新持仓）
  if (req.mode === 'navigate' || (req.headers.get('accept') || '').includes('text/html')) {
    e.respondWith(
      fetch(req)
        .then((r) => {
          const cp = r.clone();
          caches.open(VER).then((c) => c.put(req, cp));
          return r;
        })
        .catch(() => caches.match(req).then((m) => m || caches.match('./')))
    );
    return;
  }

  // 静态资源：缓存优先 + 后台静默更新
  e.respondWith(
    caches.match(req).then((m) => {
      const net = fetch(req)
        .then((r) => {
          const cp = r.clone();
          caches.open(VER).then((c) => c.put(req, cp));
          return r;
        })
        .catch(() => m);
      return m || net;
    })
  );
});
