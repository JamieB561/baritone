const http = require('http');
const fs = require('fs');
const path = require('path');

const ROOT = __dirname;
const HITS_FILE = path.join(ROOT, 'hits.json');
const PORT = process.env.PORT || 3000;

const MIME = {
  '.html': 'text/html',
  '.json': 'application/json',
  '.js': 'text/javascript',
  '.css': 'text/css',
  '.csv': 'text/csv',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.svg': 'image/svg+xml',
};

function readHits() {
  try {
    return JSON.parse(fs.readFileSync(HITS_FILE, 'utf8'));
  } catch {
    return [];
  }
}

function recordHit(req) {
  const hits = readHits();
  hits.push({
    t: new Date().toISOString(),
    ref: req.headers['referer'] || '',
    ua: req.headers['user-agent'] || '',
  });
  fs.writeFileSync(HITS_FILE, JSON.stringify(hits));
}

function statsSummary() {
  const hits = readHits();
  const now = Date.now();
  const day = 24 * 60 * 60 * 1000;
  const byDay = {};
  for (const h of hits) {
    const d = h.t.slice(0, 10);
    byDay[d] = (byDay[d] || 0) + 1;
  }
  return {
    total: hits.length,
    last24h: hits.filter(h => now - new Date(h.t).getTime() < day).length,
    last7d: hits.filter(h => now - new Date(h.t).getTime() < 7 * day).length,
    byDay,
  };
}

const STATS_PAGE = `<!DOCTYPE html>
<html><head><title>Usage — Baritone Repertoire Finder</title>
<style>
body{font-family:-apple-system,sans-serif;background:#14181D;color:#EDE7D8;padding:40px;max-width:640px;margin:0 auto;}
h1{font-size:22px;} .row{display:flex;gap:32px;margin:24px 0;}
.stat{font-size:32px;font-weight:600;} .label{font-size:12px;text-transform:uppercase;opacity:0.6;letter-spacing:0.08em;}
table{width:100%;border-collapse:collapse;margin-top:24px;font-size:14px;}
td,th{text-align:left;padding:6px 0;border-bottom:1px solid rgba(237,231,216,0.14);}
</style></head><body>
<h1>Page load stats</h1>
<div class="row" id="summary"></div>
<table><thead><tr><th>Date</th><th>Loads</th></tr></thead><tbody id="byDay"></tbody></table>
<script>
fetch('/api/stats').then(r=>r.json()).then(d=>{
  document.getElementById('summary').innerHTML =
    '<div><div class="stat">'+d.total+'</div><div class="label">Total</div></div>' +
    '<div><div class="stat">'+d.last24h+'</div><div class="label">Last 24h</div></div>' +
    '<div><div class="stat">'+d.last7d+'</div><div class="label">Last 7d</div></div>';
  const rows = Object.entries(d.byDay).sort().reverse()
    .map(([date,count])=>'<tr><td>'+date+'</td><td>'+count+'</td></tr>').join('');
  document.getElementById('byDay').innerHTML = rows || '<tr><td colspan="2">No data yet</td></tr>';
});
</script>
</body></html>`;

http.createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost');

  if (req.method === 'POST' && url.pathname === '/api/hit') {
    recordHit(req);
    res.writeHead(204);
    return res.end();
  }

  if (req.method === 'GET' && url.pathname === '/api/stats') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    return res.end(JSON.stringify(statsSummary()));
  }

  if (req.method === 'GET' && url.pathname === '/stats') {
    res.writeHead(200, { 'Content-Type': 'text/html' });
    return res.end(STATS_PAGE);
  }

  let reqPath = decodeURIComponent(url.pathname);
  if (reqPath === '/') reqPath = '/index.html';
  const filePath = path.normalize(path.join(ROOT, reqPath));
  if (!filePath.startsWith(ROOT)) {
    res.writeHead(403);
    return res.end('Forbidden');
  }

  fs.readFile(filePath, (err, data) => {
    if (err) {
      res.writeHead(404, { 'Content-Type': 'text/plain' });
      return res.end('Not found');
    }
    const ext = path.extname(filePath);
    res.writeHead(200, { 'Content-Type': MIME[ext] || 'application/octet-stream' });
    res.end(data);
  });
}).listen(PORT, () => console.log(`listening on ${PORT}`));
