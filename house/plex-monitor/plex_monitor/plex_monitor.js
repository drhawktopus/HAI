// ─── CONFIG ───────────────────────────────────────────────────────────────────
const CONFIG = {
  // Fallback port only — the LIVE remote port is read from the Plex log each run (UPnP re-maps it).
  plexPort: 28364,

  // Discord webhook URL — get it from: channel settings → Integrations → Webhooks
  // Discord webhook: one line in plex_monitor.webhook beside this script (a secret, so never in the code;
  // replace that file to rotate it). Missing file = notices are logged and skipped.
  discordWebhook: (() => { try { return require('fs').readFileSync(require('path').join(__dirname, 'plex_monitor.webhook'), 'utf8').trim(); } catch (e) { return ''; } })(),

  // How long to wait before declaring the server unreachable (ms)
  checkTimeoutMs: 10_000,
};
// ──────────────────────────────────────────────────────────────────────────────

const https = require('https');
const http  = require('http');
const fs    = require('fs');
const path  = require('path');

const STATE_FILE = path.join(__dirname, 'plex_monitor_state.json');
// Plex's own log: PLEX_LOG in the environment, else `plex_log` in plex_monitor.settings.json beside this script (a box
// that moved its Plex profile names it there), else the standard profile path.
const PLEX_LOG = (() => {
  if (process.env.PLEX_LOG) return process.env.PLEX_LOG;
  try { const s = JSON.parse(fs.readFileSync(path.join(__dirname, 'plex_monitor.settings.json'), 'utf8')); if (s.plex_log) return s.plex_log; } catch (e) {}
  return path.join(process.env.LOCALAPPDATA || '', 'Plex Media Server', 'Logs', 'Plex Media Server.log');
})();

// Plex re-maps its external port via UPnP (it drifts over time), so a hardcoded port goes stale.
// Read the CURRENT mapped port from the Plex log each run; fall back to CONFIG.plexPort if unreadable.
function getPlexPort() {
  try {
    const txt = fs.readFileSync(PLEX_LOG, 'utf8');
    const m = [...txt.matchAll(/NAT: UPnP, mapped port (\d+) to [\d.]+:32400/g)];
    if (m.length) return parseInt(m[m.length - 1][1], 10);
  } catch { /* fall through to config default */ }
  return CONFIG.plexPort;
}

function loadState() {
  try {
    return JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'));
  } catch {
    return { isDown: false, downSince: null, lastIp: null };
  }
}

function saveState(state) {
  fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 2));
}

// Fetches current external IP from ipify; returns null on failure
function getExternalIp() {
  return new Promise((resolve) => {
    const req = https.get(
      { hostname: 'api.ipify.org', path: '/', timeout: 5_000 },
      (res) => {
        let data = '';
        res.on('data', (chunk) => { data += chunk; });
        res.on('end',  () => resolve(data.trim() || null));
      }
    );
    req.on('timeout', () => { req.destroy(); resolve(null); });
    req.on('error',   () => resolve(null));
  });
}

function checkPlex(ip, port) {
  return new Promise((resolve) => {
    const req = http.get(
      {
        hostname: ip,
        port:     port,
        path:     '/identity',
        timeout:  CONFIG.checkTimeoutMs,
      },
      (res) => {
        resolve(res.statusCode < 500);
        res.resume();
      }
    );
    req.on('timeout', () => { req.destroy(); resolve(false); });
    req.on('error',   () => resolve(false));
  });
}

function sendDiscordMessage(content) {
  const body       = JSON.stringify({ content });
  if (!CONFIG.discordWebhook) { return Promise.reject(new Error('no webhook in plex_monitor.webhook - notice skipped')); }
  const webhookUrl = new URL(CONFIG.discordWebhook);

  return new Promise((resolve, reject) => {
    const req = https.request(
      {
        hostname: webhookUrl.hostname,
        path:     webhookUrl.pathname + webhookUrl.search,
        method:   'POST',
        headers: {
          'Content-Type':   'application/json',
          'Content-Length': Buffer.byteLength(body),
        },
      },
      (res) => {
        res.resume();
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve();
        } else {
          reject(new Error(`Discord webhook returned HTTP ${res.statusCode}`));
        }
      }
    );
    req.on('error', reject);
    req.write(body);
    req.end();
  });
}

function now() {
  return new Date().toLocaleString('en-US', { hour12: true });
}

async function main() {
  const state = loadState();
  const ts    = now();

  // ── Step 1: resolve external IP ─────────────────────────────────────────────
  const ip = await getExternalIp();

  if (!ip) {
    console.log(`[${ts}] Could not determine external IP — skipping check`);
    return;
  }

  // Alert if the IP changed since last run
  if (state.lastIp && state.lastIp !== ip) {
    console.log(`[${ts}] External IP changed: ${state.lastIp} → ${ip}`);
    await sendDiscordMessage(
      `🔄 **External IP changed**\n\`${state.lastIp}\` → \`${ip}\`\nPlex monitor updated automatically at ${ts}`
    );
  }

  // ── Step 2: check Plex on its CURRENT remote port (read live from the Plex log) ──
  const port = getPlexPort();
  const isUp = await checkPlex(ip, port);

  if (!isUp && !state.isDown) {
    console.log(`[${ts}] Plex went DOWN — sending alert`);
    await sendDiscordMessage(
      `🔴 **Plex is DOWN**\nNot reachable from outside at \`${ip}:${port}\`\nDetected at: ${ts}`
    );
    saveState({ isDown: true, downSince: ts, lastIp: ip });

  } else if (isUp && state.isDown) {
    console.log(`[${ts}] Plex is back UP — sending recovery notice`);
    await sendDiscordMessage(
      `🟢 **Plex is back online**\nReachable again at ${ts}\nWas down since: ${state.downSince}`
    );
    saveState({ isDown: false, downSince: null, lastIp: ip });

  } else {
    console.log(`[${ts}] Plex is ${isUp ? '✅ UP' : '❌ still DOWN'} at ${ip} — no change`);
    saveState({ ...state, lastIp: ip });
  }
}

main().catch((err) => {
  console.error('Unexpected error:', err.message);
  process.exit(1);
});
