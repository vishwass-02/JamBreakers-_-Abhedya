import { signOut, whoAmI, withToken } from './auth.js';
// One WebSocket to the laptop, shared by every tab. Auto-reconnects; re-renders at most ~10x per second.
import { useSyncExternalStore } from 'react';

const LS = (k, d) => { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } };
const SAVE = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } };

export const CLASS_INFO = {
  clean: { label: 'All clear', color: '#34e38c', plain: 'No jamming. The band looks normal.' },
  barrage: { label: 'Barrage', color: '#ff4f66', plain: 'Noise across the whole band. Hopping cannot escape it, so the radios add power and switch to a sturdier signal.' },
  random: { label: 'Random', color: '#ff7a59', plain: 'A jammer jumping between a few channels at random. The AI blocks those channels and moves to a quiet one.' },
  sweep: { label: 'Sweep', color: '#ffa24c', plain: 'A jammer racing across the band. It only brushes each channel, so the AI holds still and adds power.' },
  reactive: { label: 'Reactive', color: '#ff5fa2', plain: 'A jammer that fires only when it hears us. The AI hops fast and unpredictably so it cannot lock on.' },
  deceptive: { label: 'Deceptive', color: '#c77dff', plain: 'Fake signals that look like ours, with forged commands. Encryption rejects them; the AI moves away.' },
  accidental: { label: 'Accidental', color: '#ffd166', plain: 'Friendly interference, like Wi-Fi bursts. Not an attack, so the AI does not over-react.' },
  follower: { label: 'Follower', color: '#ff3b3b', plain: 'A jammer that chases our channel. The AI switches to secret keyed hopping it cannot predict.' },
  novel: { label: 'Novel comb', color: '#c98500', plain: 'A comb pattern the AI was never trained on (test of the unknown-jammer path).' },
  unknown: { label: 'Unknown pattern', color: '#c98500', plain: 'Not like any of the 7 jammer types the AI was trained on. It tries safe moves one by one, keeps what works and remembers it for next time.' },
  smart: { label: 'Smart', color: '#e0457b', plain: 'A learning jammer: every second it tries the tactic that has worked best so far (follow, predict, spread, pulse, spoof).' },
};
export const JAMMERS = ['barrage', 'random', 'sweep', 'reactive', 'deceptive', 'accidental', 'follower', 'smart'];
export const TACTIC_TXT = { follow: 'following our channel', predict: 'predicting our next hop', wide: 'spreading over 3 channels', pulse: 'pulsing at high power', spoof: 'spoofing our frames' };

const S = {
  conn: 'connecting', url: '', retries: 0, auth: 'checking', me: null,
  hello: null, states: {}, spectra: {}, waterfall: {},
  decisions: [], alerts: [], security: [], bench: null, report: null, caption: null,
  leaderboard: [], toast: null, lastVerdict: {},
  player: LS('aegis.player', ''), vibrate: LS('aegis.vibrate', true), server: LS('aegis.server', ''),
  phoneWorld: 'ai_on',
};
let version = 0, listeners = new Set(), pending = false, ws = null, timer = null, seq = 0;
const notify = (now = false) => {
  if (now) { version++; listeners.forEach((l) => l()); return; }
  if (pending) return;
  pending = true;
  setTimeout(() => { pending = false; version++; listeners.forEach((l) => l()); }, 100);
};
export const store = S;
export function useStore() { useSyncExternalStore((l) => { listeners.add(l); return () => listeners.delete(l); }, () => version); return S; }

// ---------- derived views
export function mode() { const st = anyState(); return st?.mode || (S.hello?.scenario ? 'scenario' : 'arena'); }
function anyState() { return S.states.main || S.states.ai_on || S.states.ai_off || null; }
export function shownWorld() {
  const st = anyState(); if (!st) return null;
  if (S.states.main) return 'main';
  if (st.mode === 'scenario') return st.focus || 'ai_on';
  return S.phoneWorld;
}
export const shownState = () => S.states[shownWorld()] || anyState();
export const shownSpectrum = () => S.spectra[shownWorld()] || S.spectra.ai_on || S.spectra.main || null;

// ---------- alerts
function alert(level, title, text, buzz = false) {
  S.alerts = [{ id: ++seq, t: Date.now(), level, title, text }, ...S.alerts].slice(0, 40);
  if (buzz && S.vibrate && navigator.vibrate) navigator.vibrate(level === 'alert' ? [140, 70, 140] : 60);
}
export function toast(text, level = 'info') { S.toast = { text, level, id: ++seq }; notify(true); setTimeout(() => { if (S.toast?.text === text) { S.toast = null; notify(true); } }, 3200); }

const ACTION_TEXT = {
  hop: 'switched channel', fast_hop: 'started fast hopping', raise_power: 'changed transmit power', lower_rate: 'changed modulation',
  relay: 'sent a relay drone',
};

function onMessage(m) {
  switch (m.type) {
    case 'hello':
      S.hello = m;
      if (m.leaderboard) S.leaderboard = m.leaderboard;
      if (m.scenario !== S.lastScenario) { S.report = null; S.caption = null; S.lastScenario = m.scenario; }
      for (const k of Object.keys(S.states)) if (!(m.view === 'side_by_side' ? ['ai_off', 'ai_on'] : ['main']).includes(k)) delete S.states[k];
      notify(true); return;
    case 'state':
      S.states[m.world] = m;
      if (m.mode === 'arena' && m.world === 'main') { delete S.states.ai_off; delete S.states.ai_on; }
      if (m.world !== 'main') delete S.states.main;
      break;
    case 'spectrum': {
      S.spectra[m.world] = m;
      const wf = (S.waterfall[m.world] ||= []); wf.unshift(m.bins); if (wf.length > 60) wf.pop();
      if (m.world === shownWorld() && m.verdict) {
        const prev = S.lastVerdict[m.world] || 'clean';
        if (m.verdict !== prev && (m.confidence ?? 0) > 0.6) {
          S.lastVerdict[m.world] = m.verdict;
          if (m.verdict !== 'clean') alert(m.verdict === 'accidental' ? 'warn' : 'alert', `${CLASS_INFO[m.verdict]?.label || m.verdict} jamming detected`,
            `${Math.round((m.confidence || 0) * 100)} % sure. ${CLASS_INFO[m.verdict]?.plain || ''}`, true);
          else alert('good', 'Band clear again', 'The classifier no longer sees a jammer.');
        }
      }
      break;
    }
    case 'decision':
      if (m.world === 'ai_off' || (m.world !== shownWorld() && shownWorld() !== 'main')) break;
      S.decisions = [m, ...S.decisions].slice(0, 30);
      if (m.action)
        alert('ai', `AI ${ACTION_TEXT[m.action] || m.action}`, m.reason + (m.latency_ms != null ? ` (${m.latency_ms.toFixed(0)} ms)` : ''));
      break;
    case 'event':
      if (m.caption) { S.caption = m; break; }
      if (m.world === 'ai_off') break;
      if (m.level === 'alert' || m.level === 'good' || m.req_id) alert(m.level === 'alert' ? 'alert' : m.level === 'good' ? 'good' : 'info', m.level === 'alert' ? 'Alert' : 'Update', m.text, m.level === 'alert');
      // a short pop-up: first sentence only; security results are already shown on their own card
      if (m.req_id && !/^Security test/.test(m.text)) toast(m.text.split(/(?<=[.!?])\s/)[0].replace(/ at \(-?\d+, ?-?\d+\)/, '').replace(/ \(J\d+\)/, ''), m.level === 'alert' ? 'alert' : 'info');
      break;
    case 'security': S.security = [m, ...S.security].slice(0, 10); break;
    case 'benchmark': S.bench = m; break;
    case 'report': S.report = m; alert('good', `${m.title}: finished`, `AI OFF ${fmt(m.ai_off?.delivery_attack_pct)} vs AI ON ${fmt(m.ai_on?.delivery_attack_pct)} delivered under attack.`, true); break;
    case 'score': S.leaderboard = m.leaderboard || []; break;
    case 'error': toast(m.message, 'alert'); break;
    default: break;
  }
  notify();
}
const fmt = (v) => (v == null ? '—' : `${v.toFixed(1)} %`);

// ---------- connection
function wsUrl() {
  const u = S.server ? S.server.replace(/^http/, 'ws').replace(/\/+$/, '') + '/ws'
    : `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`;
  return withToken(u);
}
export function connect() {
  clearTimeout(timer);
  try { ws?.close(); } catch { /* ignore */ }
  S.url = wsUrl(); S.conn = S.retries ? 'reconnecting' : 'connecting'; notify(true);
  let sock;
  try { sock = new WebSocket(S.url); } catch { return retry(); }
  ws = sock;
  sock.onopen = () => { if (ws !== sock) return; S.conn = 'online'; S.retries = 0; notify(true); };
  sock.onmessage = (e) => { try { onMessage(JSON.parse(e.data)); } catch { /* ignore bad frame */ } };
  sock.onclose = (e) => {
    if (ws !== sock) return;
    if (e.code === 4401) { S.auth = 'needed'; S.conn = 'offline'; notify(true); return; }   // signed out / session expired
    S.conn = 'offline'; notify(true); retry();
  };
  sock.onerror = () => { try { sock.close(); } catch { /* ignore */ } };
}
function retry() { S.retries++; timer = setTimeout(connect, Math.min(5000, 400 * 2 ** Math.min(S.retries, 4))); }
document.addEventListener('visibilitychange', () => { if (!document.hidden && S.conn !== 'online') { S.retries = 0; connect(); } });

export function send(action, fields = {}) {
  if (!ws || ws.readyState !== 1) { toast('Not connected to the laptop yet.', 'alert'); return false; }
  if (action === 'load_scenario' || action === 'restart' || action === 'exit_scenario') S.report = null;   // that result is over
  ws.send(JSON.stringify({ type: 'cmd', id: `m${++seq}`, player: S.player || undefined, action, ...fields }));
  return true;
}
/** check the session, then connect; the app shows the sign-in screen while S.auth === 'needed' */
export async function start() {
  const r = await whoAmI(apiBase());
  if (r.ok) { S.me = r.me; S.auth = 'ok'; notify(true); connect(); return; }
  if (r.reason === 'auth') { S.auth = 'needed'; notify(true); return; }
  S.auth = 'ok'; notify(true); connect();            // laptop unreachable right now: the socket keeps retrying
}
export function signedIn(me) { S.me = me; S.auth = 'ok'; S.retries = 0; notify(true); connect(); }
export async function logOut() { try { ws?.close(); } catch { /* ignore */ } await signOut(apiBase()); S.me = null; S.auth = 'needed'; notify(true); }
export const apiBase = () => (S.server ? S.server.replace(/\/+$/, '') : '');

export function setPref(key, val) {
  S[key] = val; SAVE('aegis.' + key, val); notify(true);
  if (key === 'server') { S.retries = 0; connect(); }
}
export function setPhoneWorld(w) { S.phoneWorld = w; notify(true); }
