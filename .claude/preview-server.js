const http = require('http');
const fs = require('fs');
const path = require('path');

const PORT = parseInt(process.env.PORT || '8080', 10);
const BACKEND = process.env.BACKEND_URL || 'http://127.0.0.1:8003';
const ROOT = path.resolve(__dirname, '..');
const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.png': 'image/png',
};

function isBackendPath(url) {
  const path = url.split('?')[0];
  return path.startsWith('/api/') || path === '/health' || path === '/metrics';
}

http.createServer((req, res) => {
  if (isBackendPath(req.url)) {
    const u = new URL(BACKEND + req.url);
    const proxy = http.request({
      hostname: u.hostname, port: u.port, path: u.pathname + u.search,
      method: req.method, headers: { ...req.headers, host: u.host },
    }, (r) => { res.writeHead(r.statusCode || 502, r.headers); r.pipe(res); });
    proxy.on('error', (e) => { res.writeHead(502); res.end('proxy: ' + e.message); });
    req.pipe(proxy);
    return;
  }
  const clean = (req.url === '/' ? '/index.html' : req.url.split('?')[0]);
  const full = path.join(ROOT, decodeURIComponent(clean));
  if (!full.startsWith(ROOT)) { res.writeHead(403); return res.end(); }
  fs.readFile(full, (err, data) => {
    if (err) { res.writeHead(404); return res.end('not found: ' + clean); }
    const ext = path.extname(full).toLowerCase();
    res.writeHead(200, { 'Content-Type': MIME[ext] || 'application/octet-stream' });
    res.end(data);
  });
}).listen(PORT, '127.0.0.1', () => console.log(`preview http://127.0.0.1:${PORT}/ → api @ ${BACKEND}`));
