import React, { useEffect, useState } from 'react';
import { useStore, mode, send, CLASS_INFO } from '../store.js';
import { api, withToken } from '../auth.js';
import { AiSwitch, Reveal } from '../ui.jsx';

let CATALOG = null;
const THEME = { mountain_pass: 'Mountain pass', city: 'City', coast: 'Coast', forest: 'Forest', airbase: 'Airbase', camp: 'Relief camp', desert: 'Desert' };
const pct = (v) => (v == null ? '—' : `${v.toFixed(0)}%`);

export default function Missions() {
  const S = useStore();
  const [list, setList] = useState(CATALOG);
  useEffect(() => { if (!CATALOG) api('', '/api/scenarios').then((r) => r.json()).then((d) => { CATALOG = (d.scenarios || d).sort((a, b) => a.order - b.order); setList(CATALOG); }).catch(() => {}); }, []);
  const scnId = S.hello?.scenario || null;
  const sc = list?.find((x) => x.id === scnId);
  const on = S.states.ai_on, off = S.states.ai_off, any = on || off || S.states.main;
  const sideBySide = (S.hello?.view || any?.view) === 'side_by_side';

  if (scnId) {
    const t = any?.scenario_t ?? 0, T = any?.scenario_duration ?? sc?.duration_s ?? 1, paused = any?.paused, speed = any?.speed || 1;
    const focus = any?.focus || 'ai_on', started = t > 0.6 || !paused;
    const rep = S.report?.scenario === scnId ? S.report : null;
    const i = list ? list.findIndex((x) => x.id === scnId) : -1, next = i >= 0 ? list[i + 1] : null;
    return (
      <div className="tab missions">
        <section className="card mission-now">
          <h2>{sc?.title || scnId}</h2>
          <p className="muted">{sc?.subtitle}</p>
          <div className="prog"><i style={{ width: `${Math.min(100, (t / T) * 100)}%` }} />{(sc?.steps || []).map((s, k) => <b key={k} style={{ left: `${(s.t / T) * 100}%` }} />)}</div>
          <div className="row-h small"><span>{rep ? 'Finished' : paused ? (started ? 'Paused' : 'Ready') : 'Running'}</span><span>{t.toFixed(0)} / {T.toFixed(0)} s</span></div>
          {S.caption && started && <p className="caption">{(sc?.steps?.[S.caption.step]?.text) || S.caption.text}</p>}
          {!started ? <button className="primary big" onClick={() => send('play')}>▶ Start</button> : (
            <div className="ctl">
              <button onClick={() => send(paused ? 'play' : 'pause')} disabled={!!rep}>{paused ? '▶ Play' : '⏸ Pause'}</button>
              <button onClick={() => send('restart')}>↺ Replay</button>
              <button className="ghost" onClick={() => send('exit_scenario')}>✕ Exit</button>
            </div>
          )}
        </section>

        {!rep && (
          <section className="card">
            <h3>Messages getting through</h3>
            <div className="vs">
              <div className={focus === 'ai_off' ? 'sel' : ''}><span>Normal radios</span><b className="bad">{pct(off?.kpis.delivery_attack_pct ?? off?.kpis.delivery_pct)}</b></div>
              <div className={focus === 'ai_on' ? 'sel' : ''}><span>With AI</span><b className="ok">{pct(on?.kpis.delivery_attack_pct ?? on?.kpis.delivery_pct)}</b></div>
            </div>
            <AiSwitch on={focus === 'ai_on'} onChange={(v) => send('toggle_ai', { enabled: v })} />
          </section>
        )}

        {sc && (
          <Reveal label="Read the briefing">
            <section className="card brief">
              <p>{sc.story}</p>
              <h4>What to watch</h4><ul>{sc.watch.map((w, k) => <li key={k}>{w}</li>)}</ul>
            </section>
          </Reveal>
        )}

        {rep && (
          <section className="card report">
            <h3>Result</h3>
            <div className="vs big"><div><span>Normal radios</span><b className="bad">{pct(rep.ai_off?.delivery_attack_pct)}</b></div><div><span>With AI</span><b className="ok">{pct(rep.ai_on?.delivery_attack_pct)}</b></div></div>
            <p className="center">of messages got through the attack</p>
            <a className="btn wide" href={withToken(`/api/report?t=${rep.t || Date.now()}`)} download>⬇ Download report</a>
            <div className="ctl">{next && <button className="primary" onClick={() => send('load_scenario', { name: next.id })}>Next mission ›</button>}
              <button onClick={() => send('restart')}>↺ Replay</button></div>
            <Reveal label="More results">
            <table><tbody>
              <tr><th></th><th>AI OFF</th><th>AI ON</th></tr>
              <tr><td>Outage</td><td>{((rep.ai_off?.outage_ms || 0) / 1000).toFixed(1)} s</td><td>{((rep.ai_on?.outage_ms || 0) / 1000).toFixed(1)} s</td></tr>
              <tr><td>Channel changes</td><td>{rep.ai_off?.hops ?? '—'}</td><td>{rep.ai_on?.hops ?? '—'}</td></tr>
              <tr><td>Hop latency</td><td>{rep.ai_off?.hop_latency_ms != null ? rep.ai_off.hop_latency_ms.toFixed(0) + ' ms' : '—'}</td><td>{rep.ai_on?.hop_latency_ms != null ? rep.ai_on.hop_latency_ms.toFixed(0) + ' ms' : '—'}</td></tr>
              <tr><td>Forged frames let in</td><td>{rep.ai_off?.frames_forged_accepted ?? 0}</td><td>{rep.ai_on?.frames_forged_accepted ?? 0}</td></tr>
            </tbody></table>
            {rep.classes_seen?.length > 0 && <p className="small">CNN identified: <b>{rep.classes_seen.join(', ')}</b></p>}
            <ol className="decs">{(rep.decisions || []).slice(0, 5).map((d, k) => <li key={k}><span className="muted">{(+d.t).toFixed(1)} s</span> {d.reason}</li>)}</ol>
            </Reveal>
          </section>
        )}
      </div>
    );
  }

  return (
    <div className="tab missions">
      <h3 className="sec">Pick a mission</h3>
      {!list && <p className="muted">Loading…</p>}
      {list?.map((x) => (
        <button key={x.id} className="card scard" style={{ '--c': CLASS_INFO[x.jammer]?.color }} onClick={() => send('load_scenario', { name: x.id })}>
          <b>{x.title}</b><span className="muted small">{x.subtitle}</span><i className="go">›</i>
        </button>
      ))}
      <section className="card">
        <div className="row-h"><h3>Free play</h3><span className="muted small">{any?.paused ? 'paused' : 'running'}</span></div>
        <div className="ctl">
          <button onClick={() => send(any?.paused ? 'play' : 'pause')}>{any?.paused ? '▶ Play' : '⏸ Pause'}</button>
          <button onClick={() => send('restart')}>↺ Start over</button>
        </div>
        <Reveal label="Options">
          <label className="toggle-row"><span><b>Normal vs AI, side by side</b><small>The big screen shows both at once.</small></span>
            <input type="checkbox" checked={sideBySide} onChange={(e) => send('set_view', { view: e.target.checked ? 'side_by_side' : 'single' })} /></label>
          <button onClick={() => send('set_speed', { speed: (any?.speed || 1) === 1 ? 2 : (any?.speed || 1) === 2 ? 4 : (any?.speed || 1) === 4 ? 10 : 1 })}>Speed {any?.speed || 1}×</button>
        </Reveal>
      </section>
    </div>
  );
}
