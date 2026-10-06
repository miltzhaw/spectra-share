#!/usr/bin/env python3
# =============================================================================
# Render real SPECTRA Semantic Causal DAG examples WITHOUT a cluster.
#
# Replays the exact Layer-2 emit() sequence a pipeline run produces and feeds
# it to SPECTRA's ACTUAL DAGBuilder (three deterministic rules, copied verbatim
# from deploy/common/demo_spectra.yaml). Produces two scenarios:
#   * impact_only    -> impact_agent tool fails            (28 events, 27 edges)
#   * citation_also  -> impact AND citation tools fail     (27 events, 26 edges)
# For each: the causal_dag_latest.json the live system writes, plus a rendered
# swimlane DAG (PNG + PDF).
#
#   python3 make_dag_example.py
# =============================================================================
import json, time, uuid, os, collections
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
from matplotlib.lines import Line2D

# ── SPECTRA reconstruction code (verbatim from demo_spectra.yaml) ────────────
class CausalEdge:
    def __init__(self, f, t, reason, fa, ta):
        self.from_event_id, self.to_event_id = f, t
        self.reason, self.from_agent, self.to_agent = reason, fa, ta

class CausalDAG:
    def __init__(self, events):
        self.events, self.edges, self.edge_details = events, set(), {}
    def add_edge(self, f, t, reason, fa, ta):
        self.edges.add((f, t))
        self.edge_details[(f, t)] = CausalEdge(f, t, reason, fa, ta)
    def to_dict(self):
        el = [{"from_event_id": f, "to_event_id": t,
               "reason": self.edge_details[(f, t)].reason,
               "from_agent": self.edge_details[(f, t)].from_agent,
               "to_agent": self.edge_details[(f, t)].to_agent}
              for (f, t) in self.edges]
        return {"num_events": len(self.events), "num_edges": len(self.edges),
                "edges": el}

class DAGBuilder:
    def build(self, events):
        dag = CausalDAG(events)
        self._delegation_rule(dag); self._intra_agent_rule(dag); self._gap(dag)
        return dag
    def _delegation_rule(self, dag):
        for ev in dag.events:
            if ev["event_type"] != "GOAL_DELEGATED": continue
            to_agent, t0 = ev["payload"].get("to"), ev["timestamp"]
            tg = [e for e in dag.events if e["agent_id"] == to_agent and e["timestamp"] > t0]
            if tg:
                nxt = min(tg, key=lambda x: x["timestamp"])
                dag.add_edge(ev["event_id"], nxt["event_id"], "delegation",
                             ev["agent_id"], to_agent)
    def _intra_agent_rule(self, dag):
        by = {}
        for ev in dag.events: by.setdefault(ev["agent_id"], []).append(ev)
        for a, evs in by.items():
            s = sorted(evs, key=lambda x: x["timestamp"])
            for i in range(len(s) - 1):
                if (s[i]["event_id"], s[i+1]["event_id"]) in dag.edges: continue
                dag.add_edge(s[i]["event_id"], s[i+1]["event_id"],
                             "intra_agent_sequence", a, a)
    def _gap(self, dag):
        by = {}
        for ev in dag.events: by.setdefault(ev["agent_id"], []).append(ev)
        for a, evs in by.items():
            s = sorted(evs, key=lambda x: x["timestamp"])
            for i in range(len(s) - 1):
                if (s[i]["event_id"], s[i+1]["event_id"]) not in dag.edges \
                        and s[i+1]["timestamp"] - s[i]["timestamp"] < 0.01:
                    dag.add_edge(s[i]["event_id"], s[i+1]["event_id"],
                                 "inferred_by_proximity", a, a)

class TraceCollector:
    def __init__(self): self.events = []
    def emit(self, et, aid, payload):
        self.events.append({"event_id": str(uuid.uuid4()), "event_type": et,
                            "agent_id": aid, "timestamp": time.time(),
                            "payload": payload})
        time.sleep(0.001)

AG = ['ingestion_agent', 'analysis_agent', 'impact_agent',
      'citation_agent', 'synthesis_agent']

def build_events(citation_fails: bool):
    col = TraceCollector()
    def ok(agent, nxt):                       # tool succeeds
        col.emit("GOAL_CREATED", agent, {})
        col.emit("REASONING_STEP", agent, {"step": "calling_llm"})
        col.emit("TOOL_INVOKED", agent, {})
        col.emit("REASONING_STEP", agent, {"step": "process_result"})
        col.emit("GOAL_COMPLETED", agent, {})
        if nxt: col.emit("GOAL_DELEGATED", agent, {"from": agent, "to": nxt})
    def fail(agent, nxt):                      # tool fails -> fallback delegates
        col.emit("GOAL_CREATED", agent, {})
        col.emit("REASONING_STEP", agent, {"step": "calling_llm"})
        col.emit("TOOL_INVOKED", agent, {})
        col.emit("GOAL_FAILED", agent, {"reason": "tool_execution_error"})
        if nxt: col.emit("GOAL_DELEGATED", agent, {"from": agent, "to": nxt})
    ok('ingestion_agent', 'analysis_agent')
    ok('analysis_agent', 'impact_agent')
    fail('impact_agent', 'citation_agent')
    (fail if citation_fails else ok)('citation_agent', 'synthesis_agent')
    ok('synthesis_agent', None)
    return col.events

# ── Renderer (swimlane, one column per agent) ────────────────────────────────
SHORT = {"GOAL_CREATED": "GOAL_CREATED", "REASONING_STEP": "REASONING_STEP",
         "TOOL_INVOKED": "TOOL_INVOKED", "GOAL_COMPLETED": "GOAL_COMPLETED",
         "GOAL_FAILED": "GOAL_FAILED", "GOAL_DELEGATED": "GOAL_DELEGATED"}
FILL = {"GOAL_FAILED": "#f2c0c0", "GOAL_COMPLETED": "#cfe6c4", "GOAL_DELEGATED": "#cddcf5"}
ECOL = {"GOAL_FAILED": "#b03a3a", "GOAL_COMPLETED": "#3a7d2c", "GOAL_DELEGATED": "#3b6cb5"}

def render(events, dag, title, out_base):
    col_idx = {a: i for i, a in enumerate(AG)}
    rows, pos, meta = {}, {}, {}
    for ev in events:
        a = ev["agent_id"]; r = rows.get(a, 0); rows[a] = r + 1
        pos[ev["event_id"]] = (col_idx[a] * 2.6, -r * 1.0)
        meta[ev["event_id"]] = ev
    fig, ax = plt.subplots(figsize=(11, 7))
    plt.rcParams.update({'font.family': 'serif'})
    for a, i in col_idx.items():
        ax.text(i * 2.6, 0.7, a.replace('_agent', ''), ha='center',
                va='bottom', fontsize=13, fontweight='bold')
    for (f, t) in dag.edges:
        reason = dag.edge_details[(f, t)].reason
        x1, y1 = pos[f]; x2, y2 = pos[t]
        if reason == "delegation":
            st = dict(arrowstyle='-|>', color='#3b6cb5', lw=1.8, linestyle='--',
                      connectionstyle='arc3,rad=-0.25', mutation_scale=16)
        else:
            st = dict(arrowstyle='-|>', color='#888', lw=1.3,
                      connectionstyle='arc3,rad=0.0', mutation_scale=13)
        ax.add_patch(FancyArrowPatch((x1, y1 - 0.16), (x2, y2 + 0.16), **st))
    for eid, ev in meta.items():
        x, y = pos[eid]; et = ev["event_type"]
        ax.add_patch(FancyBboxPatch((x - 1.05, y - 0.2), 2.1, 0.4,
                     boxstyle="round,pad=0.02,rounding_size=0.08",
                     fc=FILL.get(et, "#eeeeee"), ec=ECOL.get(et, "#999"),
                     lw=1.6 if et in ECOL else 1.0, zorder=3))
        lab = SHORT[et] + ("  △" if et == "GOAL_FAILED" else "")
        ax.text(x, y, lab, ha='center', va='center', fontsize=9,
                fontweight='bold' if et in ECOL else 'normal', zorder=4)
    leg = [Line2D([0], [0], color='#888', lw=1.3, label='intra-agent (Rule 2)'),
           Line2D([0], [0], color='#3b6cb5', lw=1.8, ls='--',
                  label='delegation / cross-agent (Rule 1)'),
           Line2D([0], [0], marker='s', color='w', markerfacecolor='#f2c0c0',
                  markeredgecolor='#b03a3a', markersize=12,
                  label='GOAL_FAILED (fault △)')]
    ax.legend(handles=leg, loc='lower center', ncol=3, fontsize=10,
              bbox_to_anchor=(0.5, -0.06), frameon=False)
    ax.set_xlim(-1.4, col_idx[AG[-1]] * 2.6 + 1.4)
    ax.set_ylim(-6.6, 1.2); ax.axis('off')
    ax.set_title(title, fontsize=13)
    fig.tight_layout()
    for ext in ('png', 'pdf'):
        fig.savefig(out_base + '.' + ext, dpi=150, bbox_inches='tight')
    plt.close(fig)

# ── GUI-style renderer (matches the SPECTRA Streamlit dashboard look) ─────────
# Dark theme, horizontal swimlanes, per-type node shapes, abbreviated labels,
# per-node timestamp offsets, and labelled delegation edges.
# Timestamps use representative per-agent LLM latencies (the standalone does not
# call an LLM) — illustrative, matching the dashboard's typical run.
from matplotlib.patches import Ellipse, Rectangle, Polygon
GFILL = {'GOAL_CREATED': '#2fbf71', 'GOAL_COMPLETED': '#2fbf71',
         'REASONING_STEP': '#4a90e2', 'TOOL_INVOKED': '#f0a020',
         'GOAL_DELEGATED': '#a78bfa', 'GOAL_FAILED': '#ef5350'}
REP_LAT = {'ingestion_agent': 3.6, 'analysis_agent': 4.3, 'impact_agent': 3.3,
           'citation_agent': 12.5, 'synthesis_agent': 3.6}

def abbr(et):
    return '.'.join(p[:4] for p in et.split('_'))

def render_gui(events, dag, title, out_base):
    COLW, ROWH, W, H = 2.25, 1.7, 1.6, 0.62
    by_agent = {}
    for ev in events:
        by_agent.setdefault(ev['agent_id'], []).append(ev)
    pos, meta = {}, {}
    for ai, a in enumerate(AG):
        for ei, ev in enumerate(by_agent.get(a, [])):
            pos[ev['event_id']] = (ei * COLW, -ai * ROWH)
            meta[ev['event_id']] = ev
    maxcol = max((len(v) for v in by_agent.values()), default=6)

    fig, ax = plt.subplots(figsize=(13, 7.5))
    fig.patch.set_facecolor('#0e1117'); ax.set_facecolor('#0e1117')
    plt.rcParams.update({'font.family': 'sans-serif'})

    # swimlane bands + agent labels + per-agent cumulative latency
    cum = 0.0
    cum_at = {}
    for ai, a in enumerate(AG):
        y = -ai * ROWH
        ax.add_patch(Rectangle((-0.95, y - 0.75), maxcol * COLW + 0.2, 1.5,
                     facecolor='#262640', edgecolor='none', zorder=0))
        ax.text(-1.35, y, a.replace('_agent', '\nagent'), color='#c9c9d6',
                ha='right', va='center', fontsize=9)
        cum += REP_LAT.get(a, 0.0); cum_at[a] = cum

    # intra-agent (green) edges
    for a, evs in by_agent.items():
        for i in range(len(evs) - 1):
            x1, y1 = pos[evs[i]['event_id']]; x2, y2 = pos[evs[i+1]['event_id']]
            ax.add_patch(FancyArrowPatch((x1 + W/2, y1), (x2 - W/2, y2),
                         arrowstyle='-|>', color='#2fbf71', lw=1.6,
                         mutation_scale=14, zorder=2))
    # delegation (purple) edges with labels
    for (f, t) in dag.edges:
        if dag.edge_details[(f, t)].reason != 'delegation': continue
        x1, y1 = pos[f]; x2, y2 = pos[t]
        ax.add_patch(FancyArrowPatch((x1, y1 - H/2), (x2, y2 + H/2),
                     arrowstyle='-|>', color='#a78bfa', lw=1.8,
                     connectionstyle='arc3,rad=-0.15', mutation_scale=15, zorder=2))
        fa = dag.edge_details[(f, t)].from_agent
        ta = dag.edge_details[(f, t)].to_agent
        ax.text((x1 + x2) / 2 + 0.2, (y1 + y2) / 2,
                f"{fa.replace('_agent','')} → {ta.replace('_agent','')} "
                f"(+{cum_at.get(fa,0):.1f}s)", color='#a78bfa', fontsize=8,
                ha='center', va='center', zorder=3)

    # nodes (shape by type) + labels + timestamps
    for eid, ev in meta.items():
        x, y = pos[eid]; et = ev['event_type']; c = GFILL[et]
        a = ev['agent_id']; idx = by_agent[a].index(ev)
        off = 0.0 if idx < 2 else REP_LAT.get(a, 0.0)
        if et in ('GOAL_CREATED', 'GOAL_COMPLETED', 'GOAL_FAILED'):
            ax.add_patch(Ellipse((x, y), W, H, facecolor=c, edgecolor='white',
                         lw=0.8, zorder=4))
        elif et == 'TOOL_INVOKED':
            ax.add_patch(Polygon([(x, y + H*0.62), (x + W*0.52, y),
                         (x, y - H*0.62), (x - W*0.52, y)], closed=True,
                         facecolor=c, edgecolor='white', lw=0.8, zorder=4))
        elif et == 'GOAL_DELEGATED':
            ax.add_patch(FancyBboxPatch((x - W/2, y - H/2), W, H,
                         boxstyle='round,pad=0.01,rounding_size=0.14',
                         facecolor=c, edgecolor='white', lw=0.8, zorder=4))
        else:  # REASONING_STEP
            ax.add_patch(Rectangle((x - W/2, y - H/2), W, H, facecolor=c,
                         edgecolor='white', lw=0.8, zorder=4))
        ax.text(x, y + 0.08, abbr(et), color='white', fontsize=8,
                fontweight='bold', ha='center', va='center', zorder=5)
        ax.text(x, y - 0.13, f"+{off:.1f}s", color='#e8e8f0', fontsize=6.5,
                ha='center', va='center', zorder=5)

    leg = [Line2D([0],[0], color='#2fbf71', lw=2, label='intra_agent_sequence'),
           Line2D([0],[0], color='#4a90e2', lw=2, label='inferred_by_proximity'),
           Line2D([0],[0], color='#a78bfa', lw=2, label='delegation (cross-agent)'),
           Line2D([0],[0], marker='o', color='#0e1117', markerfacecolor='#ef5350',
                  markersize=11, label='GOAL_FAILED')]
    lg = ax.legend(handles=leg, loc='lower center', ncol=4, fontsize=9,
                   bbox_to_anchor=(0.5, -0.05), frameon=False)
    for txt in lg.get_texts(): txt.set_color('#c9c9d6')
    if title:
        ax.set_title(title, color='white', fontsize=13)
    ax.set_xlim(-2.0, maxcol * COLW + 0.4)
    ax.set_ylim(-len(AG) * ROWH + 0.4, 1.1)
    ax.axis('off')
    fig.tight_layout()
    for ext in ('png', 'pdf'):
        fig.savefig(out_base + '.' + ext, dpi=150, bbox_inches='tight',
                    facecolor=fig.get_facecolor())
    plt.close(fig)


OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dag_example')
os.makedirs(OUT, exist_ok=True)

def main():
    for citation_fails, name, human in [
            (False, 'dag_impact_only',   'impact_agent GOAL_FAILED'),
            (True,  'dag_citation_also', 'impact + citation GOAL_FAILED')]:
        events = build_events(citation_fails)
        dag = DAGBuilder().build(events)
        et = collections.Counter(e["event_type"] for e in events)
        er = collections.Counter(e.reason for e in dag.edge_details.values())
        print(f"[{name}] {len(events)} events, {len(dag.edges)} edges")
        print("   event types:", dict(et))
        print("   edge reasons:", dict(er))
        base = os.path.join(OUT, name)
        with open(base + '.json', 'w') as f:
            json.dump(dag.to_dict(), f, indent=2)
        title = (f"SPECTRA Semantic Causal DAG  —  {len(events)} events, "
                 f"{len(dag.edges)} edges  ({human})")
        render(events, dag, title, base)                    # clean / light (paper)
        render_gui(events, dag, title, base + '_gui')        # dashboard-style (demo)
        render_gui(events, dag, "", base + '_gui_notitle')   # slide use (no title)
        print("   wrote", base + '{,_gui,_gui_notitle}.png/.pdf and .json')


if __name__ == "__main__":
    main()
