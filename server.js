const http = require('http');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const ROOT = __dirname;
const HITS_FILE = path.join(ROOT, 'hits.json');
const PORT = process.env.PORT || 3000;
const SALT = crypto.randomBytes(16).toString('hex');

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

function visitorId(req) {
  const ip = (req.headers['x-forwarded-for'] || req.socket.remoteAddress || '').split(',')[0].trim();
  const ua = req.headers['user-agent'] || '';
  return crypto.createHash('sha256').update(`${SALT}|${ip}|${ua}`).digest('hex').slice(0, 16);
}

function recordHit(req) {
  const hits = readHits();
  hits.push({
    t: new Date().toISOString(),
    ref: req.headers['referer'] || '',
    v: visitorId(req),
  });
  fs.writeFileSync(HITS_FILE, JSON.stringify(hits));
}

function uniqueCount(hits) {
  return new Set(hits.map(h => h.v)).size;
}

function statsSummary() {
  const hits = readHits();
  const now = Date.now();
  const day = 24 * 60 * 60 * 1000;
  const byDate = {};
  for (const h of hits) {
    const d = h.t.slice(0, 10);
    if (!byDate[d]) byDate[d] = [];
    byDate[d].push(h);
  }
  const byDay = {};
  for (const [d, list] of Object.entries(byDate)) {
    byDay[d] = { loads: list.length, uniques: uniqueCount(list) };
  }
  const last24h = hits.filter(h => now - new Date(h.t).getTime() < day);
  const last7d = hits.filter(h => now - new Date(h.t).getTime() < 7 * day);
  return {
    total: hits.length,
    uniqueTotal: uniqueCount(hits),
    last24h: last24h.length,
    uniqueLast24h: uniqueCount(last24h),
    last7d: last7d.length,
    uniqueLast7d: uniqueCount(last7d),
    byDay,
  };
}

const STATS_PAGE = `<!DOCTYPE html>
<html><head><title>Usage — Baritone Repertoire Finder</title>
<style>
body{font-family:-apple-system,sans-serif;background:#14181D;color:#EDE7D8;padding:40px;max-width:640px;margin:0 auto;}
h1{font-size:22px;} .row{display:flex;gap:32px;margin:24px 0;flex-wrap:wrap;}
.stat{font-size:32px;font-weight:600;} .label{font-size:12px;text-transform:uppercase;opacity:0.6;letter-spacing:0.08em;}
table{width:100%;border-collapse:collapse;margin-top:24px;font-size:14px;}
td,th{text-align:left;padding:6px 0;border-bottom:1px solid rgba(237,231,216,0.14);}
</style></head><body>
<h1>Page load stats</h1>
<div class="row" id="summary"></div>
<table><thead><tr><th>Date</th><th>Loads</th><th>Unique visitors</th></tr></thead><tbody id="byDay"></tbody></table>
<script>
fetch('/api/stats').then(r=>r.json()).then(d=>{
  const stat = (n,label)=>'<div><div class="stat">'+n+'</div><div class="label">'+label+'</div></div>';
  document.getElementById('summary').innerHTML =
    stat(d.total,'Total loads') + stat(d.uniqueTotal,'Unique visitors') +
    stat(d.last24h,'Loads (24h)') + stat(d.uniqueLast24h,'Uniques (24h)') +
    stat(d.last7d,'Loads (7d)') + stat(d.uniqueLast7d,'Uniques (7d)');
  const rows = Object.entries(d.byDay).sort().reverse()
    .map(([date,v])=>'<tr><td>'+date+'</td><td>'+v.loads+'</td><td>'+v.uniques+'</td></tr>').join('');
  document.getElementById('byDay').innerHTML = rows || '<tr><td colspan="3">No data yet</td></tr>';
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
