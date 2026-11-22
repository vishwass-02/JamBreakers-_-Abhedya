// Sign-in for the phone app. The QR on the laptop opens /app/#j=CODE: the join code is read once and removed
// from the address bar, then the user signs in with their user ID and password. The session token is kept on
// this phone until it expires (12 h) or the user signs out.
const KEY = 'abhedya_session';

const m = location.hash.match(/(?:^#|&)j=([A-Za-z0-9]{4,12})/);
export const JOIN_CODE = m ? m[1].toUpperCase() : '';
if (m) history.replaceState(null, '', location.pathname + location.search);

function read() { try { return JSON.parse(localStorage.getItem(KEY) || 'null'); } catch { return null; } }
export function session() { const s = read(); return s && s.exp > Date.now() ? s : null; }
export function token() { return session()?.token || ''; }
function save(r) { try { localStorage.setItem(KEY, JSON.stringify({ token: r.token, user: r.user, role: r.role, exp: Date.now() + r.expires_in * 1000 })); } catch { /* private mode */ } }
export function clear() { try { localStorage.removeItem(KEY); } catch { /* ignore */ } }

const hdr = () => (token() ? { Authorization: `Bearer ${token()}` } : {});
export const withToken = (url) => (token() ? `${url}${url.includes('?') ? '&' : '?'}token=${encodeURIComponent(token())}` : url);
export const api = (base, path, init = {}) => fetch(`${base}${path}`, { ...init, headers: { ...hdr(), ...(init.headers || {}) } });

/** -> {ok:true, me} | {ok:false, reason:'auth'|'offline'} */
export async function whoAmI(base = '') {
  try {
    const r = await api(base, '/api/me');
    if (r.status === 401) { clear(); return { ok: false, reason: 'auth' }; }
    return r.ok ? { ok: true, me: await r.json() } : { ok: false, reason: 'offline' };
  } catch { return { ok: false, reason: 'offline' }; }
}

export async function signIn(base, username, password, joinCode) {
  const r = await fetch(`${base}/api/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password, join_code: joinCode }) });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(d.detail || 'Sign-in failed.'), { code: d.code });
  save(d);
  return d;
}

export async function signOut(base = '') {
  try { await api(base, '/api/logout', { method: 'POST' }); } catch { /* offline: forget locally anyway */ }
  clear();
}
