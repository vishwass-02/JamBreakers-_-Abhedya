"""Mission log: an audit trail of one run, for the after-action report.

Every AI decision (with its reason, latency and any safety-guard correction), every event in the feed,
every security test, and once per simulated second a snapshot of each world's scores. Downloadable as CSV
or JSON from /api/mission-log. Capped, so a long demo cannot fill the laptop's memory.
"""
from __future__ import annotations

import csv
import io
import time
from typing import Any, Optional

MAX_ROWS = 20000
KPI_EVERY_S = 1.0
COLUMNS = ["sim_time_s", "world", "kind", "level", "class", "action", "channel", "latency_ms", "delivery_pct",
           "healthy_links", "modulation", "jammers", "safety_fixes", "safety_undos", "text"]


class MissionLog:
    def __init__(self) -> None:
        self.start("Live arena")

    def start(self, label: str) -> None:
        """A new run: scenario load, restart, view change."""
        self.label = label
        self.started_wall = time.time()
        self.rows: list[dict[str, Any]] = []
        self.dropped = 0
        self._next_kpi: dict[str, float] = {}
        self._decisions = 0
        self._guarded = 0

    def _add(self, row: dict[str, Any]) -> None:
        if len(self.rows) >= MAX_ROWS:
            self.dropped += 1
            return
        self.rows.append(row)

    def observe(self, out: list[dict], sim_t: float) -> None:
        """Called once per tick with everything the server is about to broadcast."""
        for m in out:
            kind = m.get("type")
            if kind == "decision":
                self._decisions += 1
                reason = m.get("reason", "")
                guarded = "[Safety guard" in reason or m.get("action") == "undo"
                self._guarded += guarded
                self._add({"sim_time_s": m.get("sim_time_s", round(sim_t, 3)), "world": m.get("world"),
                           "kind": "guard" if guarded else "decision", "class": m.get("class"), "action": m.get("action"),
                           "channel": m.get("to_channel"), "latency_ms": m.get("latency_ms"), "text": reason})
            elif kind == "event" and m.get("text"):
                self._add({"sim_time_s": round(sim_t, 3), "world": m.get("world"), "kind": "event",
                           "level": m.get("level"), "text": m.get("text")})
            elif kind == "security":
                self._add({"sim_time_s": round(sim_t, 3), "world": m.get("world"), "kind": "security",
                           "action": m.get("test"), "level": "accepted" if m.get("accepted") else "rejected",
                           "text": m.get("reason", "")})
            elif kind == "state":
                w = m.get("world", "main")
                t = float(m.get("sim_time_s", sim_t))
                if t < self._next_kpi.get(w, 0.0):
                    continue
                self._next_kpi[w] = t + KPI_EVERY_S
                k = m.get("kpis", {})
                cp = next((n for n in m.get("nodes", []) if n.get("id") == "CP"), {})
                self._add({"sim_time_s": round(t, 3), "world": w, "kind": "kpi", "delivery_pct": k.get("delivery_pct"),
                           "healthy_links": f"{k.get('healthy_links')}/{k.get('total_links')}",
                           "channel": cp.get("channel"), "modulation": cp.get("modulation"),
                           "jammers": len([j for j in m.get("jammers", []) if j.get("active", True)]),
                           "safety_fixes": k.get("safety_fixes"), "safety_undos": k.get("safety_undos")})

    def summary(self) -> dict[str, Any]:
        kpi = [r for r in self.rows if r["kind"] == "kpi" and r.get("delivery_pct") is not None]
        return {"label": self.label, "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.started_wall)),
                "rows": len(self.rows), "dropped": self.dropped, "decisions": self._decisions,
                "guard_corrections": self._guarded,
                "events": sum(1 for r in self.rows if r["kind"] == "event"),
                "security_tests": sum(1 for r in self.rows if r["kind"] == "security"),
                "mean_delivery_pct": round(sum(r["delivery_pct"] for r in kpi) / len(kpi), 1) if kpi else None}

    def to_json(self) -> dict[str, Any]:
        return {"summary": self.summary(), "columns": COLUMNS, "rows": self.rows}

    def to_csv(self) -> str:
        buf = io.StringIO()
        s = self.summary()
        buf.write(f"# ABHEDYA mission log: {s['label']}, started {s['started']}\n")
        buf.write(f"# {s['decisions']} AI decisions, {s['guard_corrections']} corrected or undone by the safety guard, "
                  f"mean delivery {s['mean_delivery_pct']} %\n")
        w = csv.DictWriter(buf, fieldnames=COLUMNS, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in self.rows:
            w.writerow({c: ("" if r.get(c) is None else r.get(c)) for c in COLUMNS})
        return buf.getvalue()


def label_for(scenario: Optional[dict], mission: int, view: str) -> str:
    if scenario:
        return f"Scenario: {scenario['title']}"
    return f"Live arena, mission #{mission}" + (" (side by side)" if view == "side_by_side" else "")
