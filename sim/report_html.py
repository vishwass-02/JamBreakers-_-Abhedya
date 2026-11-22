"""Downloadable end-of-mission report (GET /api/report).

Builds one self-contained HTML file from the snapshot SimArena keeps when a scenario finishes
(`SimArena.last_report`): AI OFF vs AI ON headline, a delivery-over-time chart (inline SVG, no
scripts or fonts fetched from anywhere), the KPI table, every AI decision with its reason, and the
raw mission log embedded as a CSV download. It opens offline, prints cleanly, and "Print / Save as
PDF" turns it into a PDF from any browser.

All numbers come from the simulator. The report says so on every page.
"""
from __future__ import annotations

import base64
import html
import time
from typing import Any, Optional

# backend palette: white page, navy ink, slate secondary
INK, PANEL, SLATE, PAGE = "#1a1a2e", "#2b2d42", "#8d99ae", "#ffffff"
GOOD, BAD = "#1f8a5b", "#c0392b"
OFF_COL, ON_COL = "#8d99ae", "#1a1a2e"          # AI OFF = slate, dashed · AI ON = navy, solid (never colour alone)

ACTION_WORDS = {
    "hop": "Changed channel", "fast_hop": "Hopped faster", "raise_power": "Raised power", "relay": "Rerouted via a relay",
    "lower_rate": "Slowed the data rate", "hold": "Held steady", "ignore": "Ignored (not a threat)", "none": "No change",
    "undo": "Undid a change", "survival": "Survival mode (every bit sent twice)",
}


def _e(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def _num(v: Any, unit: str = "", nd: int = 1) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        v = round(v, nd)
        if nd == 0 or float(v).is_integer():
            v = int(v)
    return f"{v}{unit}"


def _dur(ms: Any) -> str:
    if ms is None:
        return "—"
    return f"{ms / 1000:.1f} s" if ms >= 1000 else f"{ms:.0f} ms"


def _kpi_rows(on: dict, off: Optional[dict]) -> list[tuple[str, str, str, str]]:
    """(label, AI OFF, AI ON, plain-English meaning)."""
    off = off or {}
    q = lambda k, c, f: (k.get("qos") or {}).get(c, {}).get(f)  # noqa: E731
    rows = [
        ("Delivered during the attack", _num(off.get("delivery_attack_pct"), " %"), _num(on.get("delivery_attack_pct"), " %"),
         "Share of messages that got through while a jammer was on"),
        ("Delivered over the whole run", _num(off.get("delivery_total_pct"), " %"), _num(on.get("delivery_total_pct"), " %"),
         "Share of all messages that got through"),
        ("Longest outage", _dur(off.get("outage_ms")), _dur(on.get("outage_ms")),
         "Longest stretch with no message delivered"),
        ("Healthy links at the end", f"{_num(off.get('healthy_links'))}/{_num(off.get('total_links'))}" if off else "—",
         f"{_num(on.get('healthy_links'))}/{_num(on.get('total_links'))}", "Radio links still working when the run ended"),
        ("Commands on time", _num(q(off, "command", "on_time_pct"), " %"), _num(q(on, "command", "on_time_pct"), " %"),
         "Orders from the command post that arrived before their deadline"),
        ("Telemetry on time", _num(q(off, "telemetry", "on_time_pct"), " %"), _num(q(on, "telemetry", "on_time_pct"), " %"),
         "Sensor updates that arrived before their deadline"),
        ("Channel hops", _num(off.get("hops"), "", 0), _num(on.get("hops"), "", 0), "Times the network changed channel"),
        ("AI decision time", "—", _num(on.get("decision_time_ms"), " ms"), "Time the AI took to choose a countermeasure"),
        ("Forged frames accepted", _num(off.get("frames_forged_accepted"), "", 0), _num(on.get("frames_forged_accepted"), "", 0),
         "Fake messages that were wrongly trusted (should be 0)"),
        ("Safety-guard corrections", _num(off.get("safety_fixes"), "", 0), _num(on.get("safety_fixes"), "", 0),
         "Times the safety guard overrode a risky AI choice"),
    ]
    return rows


def _chart(rows: list[dict], duration: float) -> tuple[str, str]:
    """Delivery % over time, AI OFF vs AI ON, with the attack window shaded. Returns (svg, data-table html)."""
    series: dict[str, list[tuple[float, float]]] = {}
    attack: list[float] = []
    for r in rows:
        if r.get("kind") != "kpi" or r.get("delivery_pct") is None:
            continue
        w = "ai_on" if r.get("world") in ("ai_on", "main") else "ai_off"
        series.setdefault(w, []).append((float(r["sim_time_s"]), float(r["delivery_pct"])))
        if w == "ai_on" and (r.get("jammers") or 0) > 0:
            attack.append(float(r["sim_time_s"]))
    if not series:
        return "<p class='mute'>No timeline was recorded for this run.</p>", ""
    W, H, L, R, T, B = 760, 280, 46, 92, 14, 34
    pw, ph = W - L - R, H - T - B
    tmax = max(duration, max(t for s in series.values() for t, _ in s)) or 1.0
    X = lambda t: L + pw * t / tmax  # noqa: E731
    Y = lambda v: T + ph * (1 - v / 100)  # noqa: E731
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Messages delivered over time, AI OFF versus AI ON" class="chart">']
    if attack:   # shade where a jammer was on (one band from first to last attacked second)
        a0, a1 = min(attack) - 0.5, max(attack) + 0.5
        out.append(f'<rect x="{X(max(a0, 0)):.1f}" y="{T}" width="{X(min(a1, tmax)) - X(max(a0, 0)):.1f}" height="{ph}" fill="#eef0f4"/>')
        out.append(f'<text x="{X(max(a0, 0)) + 6:.1f}" y="{T + ph - 8}" class="ax">Jammer on</text>')
    for v in (0, 25, 50, 75, 100):
        out.append(f'<line x1="{L}" x2="{L + pw}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="#e3e6ec" stroke-width="1"/>')
        out.append(f'<text x="{L - 8}" y="{Y(v) + 4:.1f}" text-anchor="end" class="ax">{v}%</text>')
    step = 10 if tmax <= 120 else 30
    t = 0.0
    while t <= tmax + 1e-6:
        out.append(f'<text x="{X(t):.1f}" y="{H - 12}" text-anchor="middle" class="ax">{int(t)} s</text>')
        t += step
    out.append(f'<line x1="{L}" x2="{L + pw}" y1="{Y(0):.1f}" y2="{Y(0):.1f}" stroke="{SLATE}" stroke-width="1"/>')
    labels = []
    for w, col, dash, name in (("ai_off", OFF_COL, "6 4", "AI OFF"), ("ai_on", ON_COL, "", "AI ON")):
        pts = series.get(w)
        if not pts:
            continue
        d = " ".join(f"{'M' if i == 0 else 'L'}{X(t):.1f},{Y(v):.1f}" for i, (t, v) in enumerate(pts))
        out.append(f'<path d="{d}" fill="none" stroke="{col}" stroke-width="2" stroke-linejoin="round"'
                   + (f' stroke-dasharray="{dash}"' if dash else "") + "/>")
        for t, v in pts:   # invisible hover targets with a native tooltip
            out.append(f'<circle cx="{X(t):.1f}" cy="{Y(v):.1f}" r="6" fill="transparent"><title>{name} · {t:.0f} s · {v:.1f}% delivered</title></circle>')
        lt, lv = pts[-1]
        labels.append([Y(lv), f'{name} {lv:.0f}%', col])
    labels.sort()
    if len(labels) == 2 and labels[1][0] - labels[0][0] < 16:   # keep end labels apart
        mid = (labels[0][0] + labels[1][0]) / 2
        labels[0][0], labels[1][0] = mid - 8, mid + 8
    for y, txt, _col in labels:
        out.append(f'<text x="{L + pw + 8}" y="{y + 4:.1f}" class="lab">{_e(txt)}</text>')
    out.append("</svg>")
    # table view of the same data (every 5 s)
    on = dict(series.get("ai_on", []))
    off = dict(series.get("ai_off", []))
    times = sorted(set(on) | set(off))
    keep = [t for t in times if abs((t - 0.5) % 5) < 1e-6] or times
    trs = "".join(f"<tr><td>{t:.0f} s</td><td>{_num(off.get(t), ' %')}</td><td>{_num(on.get(t), ' %')}</td></tr>" for t in keep)
    table = (f"<details><summary>Show the chart as a table</summary><table class='mini'><thead><tr><th>Time</th>"
             f"<th>AI OFF</th><th>AI ON</th></tr></thead><tbody>{trs}</tbody></table></details>")
    return "".join(out), table


def _watch(w: Any) -> str:
    if not w:
        return ""
    if isinstance(w, (list, tuple)):
        return "<p><b>What to watch:</b></p><ul>" + "".join(f"<li>{_e(x)}</li>" for x in w) + "</ul>"
    return f"<p><b>What to watch:</b> {_e(w)}</p>"


def build_report_html(snap: dict) -> str:
    msg, sc = snap["msg"], snap.get("scenario") or {}
    on, off = msg.get("ai_on") or {}, msg.get("ai_off")
    rows = snap.get("rows") or []
    finished = time.strftime("%d %b %Y, %H:%M", time.localtime(snap.get("finished_wall", time.time())))
    title = msg.get("title") or sc.get("title") or msg.get("scenario")

    on_a, off_a = on.get("delivery_attack_pct"), (off or {}).get("delivery_attack_pct")
    if off_a is not None and on_a is not None:
        gap = on_a - off_a
        verdict = (f"With the AI, <b>{_num(on_a, '%')}</b> of messages got through during the attack, "
                   f"against <b>{_num(off_a, '%')}</b> with normal fixed-hopping radios "
                   f"({'+' if gap >= 0 else '−'}{abs(gap):.1f} points).")
    else:
        verdict = f"With the AI, <b>{_num(on_a, '%')}</b> of messages got through during the attack."

    kpis = "".join(
        f"<tr><th scope='row'>{_e(a)}<span class='why'>{_e(d)}</span></th><td>{_e(o)}</td><td class='on'>{_e(n)}</td></tr>"
        for a, o, n, d in _kpi_rows(on, off))

    decs = [r for r in rows if r.get("kind") in ("decision", "guard") and r.get("world") in ("ai_on", "main")]
    if not decs:   # fall back to the report message's own list
        decs = [{"sim_time_s": d["t"], "class": d["class"], "action": d["action"], "text": d["reason"], "kind": "decision",
                 "latency_ms": d.get("latency_ms")} for d in msg.get("decisions", [])]
    dec_rows = "".join(
        f"<tr><td class='nw'>{float(d.get('sim_time_s') or 0):.1f} s</td><td>{_e(d.get('class'))}</td>"
        f"<td>{_e(ACTION_WORDS.get(d.get('action'), d.get('action')))}{' <span class=tag>safety guard</span>' if d.get('kind') == 'guard' else ''}</td>"
        f"<td class='reason'>{_e(d.get('text'))}</td></tr>" for d in decs[:40])
    more = f"<p class='mute'>Showing the first 40 of {len(decs)} decisions; all are in the CSV below.</p>" if len(decs) > 40 else ""

    svg, data_table = _chart(rows, float(msg.get("duration_s") or sc.get("duration_s") or 0))
    csv = snap.get("csv") or ""
    csv_href = "data:text/csv;base64," + base64.b64encode(csv.encode("utf-8")).decode()
    csv_name = f"ABHEDYA_{msg.get('scenario')}_mission_log.csv"
    classes = ", ".join(msg.get("classes_seen") or []) or "none"
    lg = snap.get("log") or {}

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ABHEDYA mission report · {_e(title)}</title>
<style>
  :root {{ --ink:{INK}; --panel:{PANEL}; --slate:{SLATE}; --page:{PAGE}; --line:#e3e6ec; --soft:#f4f5f8; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:#eceef2; color:var(--ink); font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }}
  .sheet {{ max-width:900px; margin:24px auto; background:var(--page); padding:36px 44px 40px; border-radius:6px;
           box-shadow:0 2px 14px rgba(26,26,46,.10); }}
  header {{ display:flex; justify-content:space-between; align-items:flex-start; gap:16px; border-bottom:3px solid var(--ink);
           padding-bottom:14px; margin-bottom:22px; }}
  .brand {{ font-weight:800; letter-spacing:.18em; font-size:13px; color:var(--panel); }}
  h1 {{ margin:4px 0 2px; font-size:28px; line-height:1.2; }}
  .sub {{ color:#5d6678; margin:0; }}
  .meta {{ text-align:right; font-size:13px; color:#5d6678; white-space:nowrap; }}
  .badge {{ display:inline-block; border:1px solid var(--slate); color:var(--panel); border-radius:20px; padding:2px 10px;
           font-size:12px; font-weight:600; margin-top:6px; }}
  h2 {{ font-size:13px; letter-spacing:.14em; text-transform:uppercase; color:var(--panel); margin:30px 0 10px; }}
  .verdict {{ background:var(--soft); border-left:4px solid var(--ink); padding:14px 18px; font-size:17px; margin:0; }}
  .tiles {{ display:grid; grid-template-columns:repeat(2,1fr); gap:12px; margin-top:14px; }}
  .tile {{ border:1px solid var(--line); border-radius:6px; padding:12px 16px; }}
  .tile .k {{ font-size:12px; color:#5d6678; text-transform:uppercase; letter-spacing:.1em; }}
  .tile .v {{ font-size:30px; font-weight:750; font-variant-numeric:tabular-nums; }}
  .tile.on {{ border-color:var(--ink); }}
  .chart {{ width:100%; height:auto; display:block; }}
  .chart .ax {{ font-size:11px; fill:#5d6678; }}
  .chart .lab {{ font-size:12px; font-weight:650; fill:var(--ink); }}
  .legend {{ display:flex; gap:18px; font-size:13px; color:#5d6678; margin:4px 0 6px; }}
  .legend i {{ display:inline-block; width:26px; height:0; border-top:2px solid; vertical-align:middle; margin-right:6px; }}
  table {{ width:100%; border-collapse:collapse; font-variant-numeric:tabular-nums; }}
  th, td {{ text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); vertical-align:top; }}
  thead th {{ font-size:12px; text-transform:uppercase; letter-spacing:.08em; color:#5d6678; border-bottom:2px solid var(--ink); }}
  .kpi td {{ width:120px; font-weight:600; }}
  .kpi td.on {{ color:var(--ink); }}
  .why {{ display:block; font-weight:400; font-size:12.5px; color:#6b7385; }}
  .nw {{ white-space:nowrap; }}
  .reason {{ font-size:13.5px; color:#3b4256; }}
  .tag {{ font-size:11px; border:1px solid var(--slate); border-radius:10px; padding:0 6px; color:var(--panel); }}
  .mini {{ max-width:360px; margin-top:8px; }}
  details summary {{ cursor:pointer; color:var(--panel); font-size:13px; margin-top:6px; }}
  .story p {{ margin:6px 0; }}
  .mute {{ color:#6b7385; font-size:13px; }}
  .actions {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:12px; }}
  .btn {{ background:var(--ink); color:#fff; border:0; border-radius:6px; padding:9px 16px; font:600 14px system-ui,sans-serif;
         cursor:pointer; text-decoration:none; }}
  .btn.ghost {{ background:#fff; color:var(--ink); border:1px solid var(--slate); }}
  footer {{ margin-top:34px; padding-top:12px; border-top:1px solid var(--line); font-size:12px; color:#6b7385; }}
  @media (max-width:640px) {{ .sheet {{ padding:22px 18px; margin:0; border-radius:0; }} header {{ flex-direction:column; }}
                              .meta {{ text-align:left; }} .kpi td {{ width:auto; }} }}
  @media print {{ body {{ background:#fff; }} .sheet {{ box-shadow:none; margin:0; max-width:none; padding:0; }}
                  .actions, details summary {{ display:none; }} h2 {{ break-after:avoid; }} tr, .tile {{ break-inside:avoid; }}
                  @page {{ margin:16mm; }} }}
</style></head>
<body><main class="sheet">
<header>
  <div><div class="brand">ABHEDYA · MISSION REPORT</div>
    <h1>{_e(title)}</h1><p class="sub">{_e(sc.get("subtitle"))}</p></div>
  <div class="meta">{_e(finished)}<br>Jammer: <b>{_e(msg.get("jammer"))}</b><br>Mission time: {_num(msg.get("duration_s"), " s")}
    <br><span class="badge">Simulated data</span></div>
</header>

<h2>Result</h2>
<p class="verdict">{verdict}</p>
<div class="tiles">
  <div class="tile"><div class="k">Normal radios (AI OFF)</div><div class="v">{_num(off_a, "%")}</div></div>
  <div class="tile on"><div class="k">With AI (AI ON)</div><div class="v">{_num(on_a, "%")}</div></div>
</div>
<p class="mute">Expected for this mission: {_e(msg.get("expected"))}</p>
<div class="actions">
  <button class="btn" onclick="window.print()">Print / Save as PDF</button>
  <a class="btn ghost" href="{csv_href}" download="{_e(csv_name)}">Download raw log (CSV)</a>
</div>

<h2>Messages delivered over time</h2>
<div class="legend"><span><i style="border-color:{OFF_COL};border-top-style:dashed"></i>AI OFF · fixed hopping</span>
  <span><i style="border-color:{ON_COL}"></i>AI ON · ABHEDYA</span></div>
{svg}
{data_table}

<h2>Side-by-side numbers</h2>
<table class="kpi"><thead><tr><th>Measure</th><th>AI OFF</th><th>AI ON</th></tr></thead><tbody>{kpis}</tbody></table>

<h2>What the AI did</h2>
<p class="mute">Jamming types the AI recognised: <b>{_e(classes)}</b>. Decisions: {len(decs)} · safety-guard corrections: {_num(lg.get("guard_corrections"), "", 0)}.</p>
<table><thead><tr><th>Time</th><th>Saw</th><th>Action</th><th>Why</th></tr></thead><tbody>
{dec_rows or "<tr><td colspan=4 class='mute'>No countermeasures were needed.</td></tr>"}</tbody></table>
{more}

<h2>The mission</h2>
<div class="story"><p>{_e(sc.get("story"))}</p>
{_watch(sc.get("watch"))}
<p><b>How the AI is meant to respond:</b> {_e(sc.get("ai_response"))}</p></div>

<footer>Generated by the ABHEDYA simulator (team JamBreakers, BITS2WAVE). All figures come from a software simulation with the same
seed and the same attack in both worlds; they are not measurements from radio hardware. Run speed: {_e(snap.get("speed", 1))}x ·
log rows: {_e(lg.get("rows", len(rows)))}.</footer>
</main></body></html>"""
