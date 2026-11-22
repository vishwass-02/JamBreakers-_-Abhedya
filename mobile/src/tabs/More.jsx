import React, { useEffect, useState } from 'react';
import { useStore, send, setPref, store, logOut } from '../store.js';
import { api, withToken } from '../auth.js';
import { Reveal } from '../ui.jsx';
import Signal from './Signal.jsx';

const TESTS = [
  ['legit', 'Real message', 'A normal message from drone D1.'],
  ['tamper', 'Tamper', 'Change one bit of a real message.'],
  ['spoof', 'Fake command', 'Pretend to be D1 without the key.'],
  ['replay', 'Replay', 'Send an old message again.'],
  ['eavesdrop', 'Eavesdrop', 'What an interceptor actually sees.'],
  ['desync', 'Sync loss', 'One drone misses a channel switch; watch it rejoin.'],
  ['impersonate', 'Impersonate', 'A captured drone, with its keys, pretends to be D1.'],
  ['reboot', 'Reboot', 'A drone restarts; it gets a new session key, no nonce reuse.'],
  ['capture', 'Capture', 'A drone is captured: revoke it and rotate the group key.'],
  ['clock_loss', 'Clock loss', 'The command post clock fails: holdover, then a backup time master.'],
];
const PLAIN = {
  legit: 'Real messages get through normally.',
  tamper: 'A changed message is detected and thrown away.',
  spoof: 'Without the secret key, a fake command is rejected.',
  replay: 'An old message sent again is recognised and ignored.',
  eavesdrop: 'An eavesdropper only sees scrambled bytes.',
  desync: 'The drone lost the beat for a moment and rejoined by itself.',
  impersonate: 'A captured drone cannot pretend to be a different one.',
  reboot: 'After a restart the drone gets a fresh key, so nothing is reused.',
  capture: 'The captured drone is cut off and everyone else gets a new key.',
  clock_loss: 'Clocks kept time, then a backup timekeeper took over.',
};
const hexGroups = (h, n = 48) => (h || '').slice(0, n * 2).match(/.{1,2}/g)?.join(' ') + ((h || '').length > n * 2 ? ' …' : '');

let installEvt = null;
window.addEventListener('beforeinstallprompt', (e) => { e.preventDefault(); installEvt = e; });

export default function More() {
  const S = useStore();
  const [name, setName] = useState(S.player);
  const [bench, setBench] = useState(null);
  const [server, setServer] = useState(S.server);
  useEffect(() => { api('', '/api/benchmark/latest').then((r) => (r.ok ? r.json() : null)).then(setBench).catch(() => {}); }, [S.bench?.status]);
  const b = S.bench?.status === 'done' ? S.bench : bench;
  const running = S.bench && S.bench.status === 'running';
  const sec = S.security[0];
  const fp = S.hello?.key_fingerprint;
  const verdict = sec && (['legit', 'reboot', 'desync', 'clock_loss'].includes(sec.test) ? sec.accepted : !sec.accepted);
  const avg = (d) => { const r = (b?.rows || []).filter((x) => x.defender === d).map((x) => x.delivery_attack_pct ?? x.delivery_pct).filter((v) => v != null); return r.length ? r.reduce((p, q) => p + q, 0) / r.length : null; };
  const aiAvg = avg('hybrid') ?? avg('aegis'), offAvg = avg('fixed_hopping');
  return (
    <div className="tab more">
      <section className="card">
        <h3>Try to hack it</h3>
        <p className="muted small">Each button sends a real encrypted message, or an attack on one.</p>
        <div className="sec-grid">{TESTS.slice(0, 4).map(([id, label, d]) => <button key={id} onClick={() => send('security_test', { test: id })}><b>{label}</b><small>{d}</small></button>)}</div>
        {sec && (
          <div className={`sec-res ${verdict ? 'ok' : 'bad'}`}>
            <b>{verdict ? '✓ ' : '✕ '}{TESTS.find((t) => t[0] === sec.test)?.[1]}: {sec.test === 'legit' ? 'accepted' : sec.test === 'eavesdrop' ? 'unreadable' : verdict ? 'blocked' : 'got through'}</b>
            <p>{PLAIN[sec.test] || sec.reason}</p>
            <Reveal label="Technical details">
              <p className="small muted">{sec.reason}</p>
              {sec.header_hex && <p className="mono hex"><span>header</span>{hexGroups(sec.header_hex)}</p>}
              {sec.ciphertext_hex && <p className="mono hex"><span>encrypted</span>{hexGroups(sec.ciphertext_hex)}</p>}
            </Reveal>
          </div>
        )}
        <Reveal label="More tests">
          <div className="sec-grid">{TESTS.slice(4).map(([id, label, d]) => <button key={id} onClick={() => send('security_test', { test: id })}><b>{label}</b><small>{d}</small></button>)}</div>
        </Reveal>
      </section>

      <section className="card">
        <h3>Test results</h3>
        {aiOff(aiAvg, offAvg)}
        <Reveal label="Full table">
          {b?.rows?.length ? <BenchTable b={b} /> : <p className="muted">No results yet.</p>}
          <button onClick={() => send('run_benchmark', { quick: true })} disabled={running}>{running ? `Running… ${Math.round((S.bench.progress || 0) * 100)} %` : 'Run a quick test (≈ 1 min)'}</button>
        </Reveal>
      </section>

      <section className="card">
        <h3>Your name</h3>
        <div className="inrow"><input value={name} maxLength={24} placeholder="Shown on your jammers" onChange={(e) => setName(e.target.value)} />
          <button onClick={() => setPref('player', name.trim())} disabled={name.trim() === S.player}>Save</button></div>
        {S.leaderboard.length > 0 && <ol className="lb">{S.leaderboard.slice(0, 3).map((p) => <li key={p.player}><b>{p.player}</b><span>{p.attacks} jammer{p.attacks > 1 ? 's' : ''}</span></li>)}</ol>}
      </section>

      <Reveal label="Signal analysis (expert)"><Signal /></Reveal>

      {S.me && !S.me.local && (
        <div className="card account">
          <span>Signed in as <b>{S.me.user}</b> · {S.me.role === 'commander' ? 'can control missions' : 'view only'}</span>
          <button className="btn" onClick={logOut}>Sign out</button>
        </div>
      )}
      <Reveal label="Settings">
        <section className="card">
          <label className="toggle-row"><span><b>Vibrate on alerts</b></span>
            <input type="checkbox" checked={S.vibrate} onChange={(e) => setPref('vibrate', e.target.checked)} /></label>
          <p className="small"><b>Laptop address</b></p>
          <div className="inrow"><input value={server} placeholder={`${location.host} (this page)`} onChange={(e) => setServer(e.target.value)} />
            <button onClick={() => setPref('server', server.trim())}>Connect</button></div>
          <p className="muted small">Connected to <code>{S.url.split('?')[0].replace(/^wss?:\/\//, '').replace('/ws', '')}</code> · {S.conn}</p>
          <a className="btn" href={withToken('/api/mission-log')} download>Download the mission log</a>
          {installEvt && <button className="primary" onClick={() => installEvt.prompt()}>Install ABHEDYA on this phone</button>}
          <p className="muted small">Key fingerprint <code>{fp || '—'}</code></p>
        </section>
      </Reveal>
      <p className="muted small center">ABHEDYA · team JamBreakers</p>
    </div>
  );
}

function aiOff(ai, off) {
  if (ai == null || off == null) return <p className="muted">No results yet.</p>;
  return (
    <>
      <div className="vs"><div><span>Normal radios</span><b className="bad">{off.toFixed(0)}%</b></div><div><span>With AI</span><b className="ok">{ai.toFixed(0)}%</b></div></div>
      <p className="muted small center">messages delivered, averaged over every jammer type</p>
    </>
  );
}

function BenchTable({ b }) {
  const all = [...new Set(b.rows.map((r) => r.defender))];
  const KEY = ['fixed_hopping', 'max_power', 'afh_power', 'aegis'];
  const defs = all.length > 4 && KEY.every((d) => all.includes(d)) ? KEY : all;
  const types = [...new Set(b.rows.map((r) => r.jammer))];
  const get = (j, d) => b.rows.find((r) => r.jammer === j && r.defender === d);
  const name = { none: 'None', fixed_hopping: 'Fixed', energy: 'Energy', fhss_keyed: 'Secret hop', max_power: 'Brute force', afh_power: 'Rules', rl: 'RL', aegis: 'AI', hybrid: 'AI+RL' };
  const val = (r) => r?.delivery_attack_pct ?? r?.delivery_pct;
  return (
    <div className="bench-wrap"><table className="bench"><thead><tr><th>Jammer</th>{defs.map((d) => <th key={d}>{name[d] || d}</th>)}</tr></thead>
      <tbody>{types.map((j) => <tr key={j}><td>{j}</td>{defs.map((d) => { const v = val(get(j, d)); return <td key={d} className={v == null ? '' : v >= 90 ? 'ok' : v >= 60 ? 'warn' : 'bad'}>{v == null ? '—' : v.toFixed(0)}</td>; })}</tr>)}</tbody>
    </table></div>
  );
}
