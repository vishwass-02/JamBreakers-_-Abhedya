import React from 'react';
import { useStore, shownState, shownSpectrum, shownWorld, mode, send, setPhoneWorld, CLASS_INFO, TACTIC_TXT } from '../store.js';
import { AiSwitch, Gauge, Reveal, SIMPLE, timeAgo } from '../ui.jsx';

// One plain sentence about how the network is doing.
function status(st, conn) {
  if (conn !== 'online' || !st) return { tone: 'idle', title: 'Connecting…', text: 'Join the same Wi-Fi as the laptop.' };
  const k = st.kpis, active = st.jammers.filter((j) => j.active), d = Math.round(st.mode === 'scenario' ? (k.delivery_attack_pct ?? k.delivery_pct) : k.delivery_pct);
  if (!active.length) return { tone: 'ok', title: 'All clear', text: 'No jamming. Every link is working.' };
  if (d >= 90) return { tone: 'ok', title: 'Under attack, holding', text: 'Messages are still getting through.' };
  if (d >= 60) return { tone: 'warn', title: 'Struggling', text: 'Some messages are being lost.' };
  return { tone: 'bad', title: 'Losing contact', text: 'Most messages are being lost.' };
}

export default function Live({ go }) {
  const S = useStore();
  const st = shownState(), sp = shownSpectrum(), scn = mode() === 'scenario', world = shownWorld();
  const s = status(st, S.conn);
  const k = st?.kpis;
  const aiOn = world === 'main' ? !!st?.ai_enabled : world === 'ai_on';
  const flip = (on) => {
    if (world === 'main' || scn) send('toggle_ai', { enabled: on });
    else setPhoneWorld(on ? 'ai_on' : 'ai_off');
  };
  const atk = scn && k?.delivery_attack_pct != null;
  const delivery = k ? (atk ? k.delivery_attack_pct : k.delivery_pct) : null;
  const active = st?.jammers.filter((j) => j.active) || [];
  // no attack (yet): the reading is honestly 100 % whether the AI is on or off, so say so instead of looking broken
  const calm = !!st && !active.length && !(atk && k.delivery_attack_pct < 99.5);
  const canControl = !S.me || S.me.local || S.me.role === 'commander';
  const testAttack = () => {
    const ns = (st?.nodes || []).filter((n) => n.pos);
    if (!ns.length) return;
    const cx = ns.reduce((a, n) => a + n.pos[0], 0) / ns.length, cy = ns.reduce((a, n) => a + n.pos[1], 0) / ns.length;
    send('launch_jammer', { jammer_type: 'follower', pos: [Math.round(cx + 30), Math.round(cy + 30)] });
  };
  const v = sp?.verdict && sp.verdict !== 'clean' ? sp.verdict : null;
  const what = v ? SIMPLE[v] || SIMPLE.unknown : null;
  const lowBat = (st?.nodes || []).filter((n) => n.battery_pct != null).sort((a, b) => a.battery_pct - b.battery_pct)[0];
  const est = st?.estimates?.[0], cp = st?.nodes.find((n) => n.kind === 'command_post');
  const lastDec = S.decisions[0];

  return (
    <div className="tab live">
      <section className={`card status ${s.tone}`}>
        <div className="st-icon">{s.tone === 'ok' ? '✓' : s.tone === 'warn' ? '!' : s.tone === 'bad' ? '✕' : '…'}</div>
        <div><h2>{s.title}</h2><p>{s.text}</p></div>
      </section>

      <section className="card hero">
        <Gauge value={delivery} label={calm ? 'getting through: no jammer yet' : 'of messages getting through'} />
        <AiSwitch on={aiOn} onChange={flip} />
        <p className="hint-line">{aiOn ? 'AI is protecting the network' : 'AI is off: normal radios, no protection'}</p>
        {calm && (
          <div className="calm-note">
            <p>With no jammer, every radio works, so <b>AI OFF and AI ON both read 100 %</b>. Start an attack to see the difference.</p>
            {canControl && !scn && <button className="btn primary wide" onClick={testAttack}>Launch a test jammer</button>}
          </div>
        )}
      </section>

      <section className="card threat-simple" style={{ '--c': v ? CLASS_INFO[v]?.color : 'var(--mute)' }}>
        {!active.length ? (
          <>
            <h3>No jammer</h3>
            <p className="muted">Go to <button className="link" onClick={() => go('map')}>Attack</button> and drop one on the map.</p>
          </>
        ) : !aiOn ? (
          <>
            <h3>{active.length > 1 ? `${active.length} jammers attacking` : 'A jammer is attacking'}</h3>
            <p className="muted">Turn the AI on to see what it is and fight back.</p>
          </>
        ) : (
          <>
            <div className="line"><span className="lbl">Jammer</span><b>{v ? CLASS_INFO[v]?.label : 'Identifying…'}</b></div>
            {what && <p className="muted">{what[0]}</p>}
            <div className="line"><span className="lbl">AI is</span><b>{what ? what[1] : 'Listening…'}</b></div>
          </>
        )}
      </section>

      <section className="tiles three">
        <Tile label="Links up" value={k ? `${k.healthy_links}/${k.total_links}` : '—'} tone={k && k.healthy_links < k.total_links ? 'warn' : 'ok'} />
        <Tile label="Fake messages" value={k ? `${k.frames_rejected ?? 0}` : '—'} sub="blocked" tone="ok" />
        <Tile label="Lowest battery" value={lowBat ? `${Math.round(lowBat.battery_pct)}%` : '—'} tone={lowBat && lowBat.battery_pct < 35 ? 'warn' : ''} />
      </section>

      <Reveal label="Show details">
        <section className="card">
          <ul className="facts">
            {k && <li><span>Time to switch channel</span><b>{k.hop_latency_ms != null ? `${k.hop_latency_ms.toFixed(0)} ms` : '—'}</b></li>}
            {k && <li><span>Time with no contact</span><b>{(k.outage_ms / 1000).toFixed(1)} s</b></li>}
            {k && <li><span>Channel changes</span><b>{k.hops}</b></li>}
            {st && cp && <li><span>Radio</span><b>channel {cp.channel ?? '—'} · {cp.modulation === 'BPSK_R2' ? 'survival mode' : cp.modulation}</b></li>}
            {k && <li><span>Bad AI orders stopped</span><b>{(k.safety_fixes ?? 0) + (k.safety_undos ?? 0)}</b></li>}
            {est && cp && aiOn && <li><span>Jammer found</span><b>~{Math.round(Math.hypot(est.pos[0] - cp.pos[0], est.pos[1] - cp.pos[1]))} m away</b></li>}
            {v && aiOn && <li><span>AI certainty</span><b>{Math.round((sp.confidence || 0) * 100)}%</b></li>}
          </ul>
          {lastDec && aiOn && <p className="small"><b>Last move:</b> {lastDec.reason}</p>}
          {st?.jammers.filter((j) => j.tactic).map((j) => <p key={j.id} className="small muted">{j.id} is a learning jammer, now {TACTIC_TXT[j.tactic] || j.tactic}.</p>)}
        </section>
        <section className="card alerts">
          <h3>Recent events</h3>
          {!S.alerts.length && <p className="muted">Nothing yet.</p>}
          <ul>{S.alerts.slice(0, 6).map((a) => (
            <li key={a.id} className={a.level}><i /><div><b>{a.title}</b><span className="when">{timeAgo(a.t)}</span></div></li>
          ))}</ul>
        </section>
      </Reveal>
    </div>
  );
}

function Tile({ label, value, sub, tone = '' }) {
  return <div className={`tile ${tone}`}><span>{label}</span><b>{value}</b>{sub && <small>{sub}</small>}</div>;
}
