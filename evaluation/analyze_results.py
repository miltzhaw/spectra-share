#!/usr/bin/env python3
# =============================================================================
# SPECTRA results analysis + paper figure regeneration
#
# Parses spectra_measure.sh result files for two models, prints the statistics
# used in the paper (Table crossmodel, citation CIs, latency, overhead), and
# regenerates fig4_failures.pdf and fig6_latency.pdf in the paper/ folder.
#
# USAGE (from the evaluation/ folder):
#   python3 analyze_results.py results/local/results_tinyllama_50.txt results/local/results_mistral_50.txt
#   python3 analyze_results.py --no-figures results/robopaas/<file> results/robopaas/<file>
# =============================================================================
import sys, re, math, statistics, os

FIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'paper')
AGENTS = ['ingestion_agent', 'analysis_agent', 'impact_agent',
          'citation_agent', 'synthesis_agent']

RUN_RE = re.compile(
    r'^\s*(\d+)\s*\|\s*([\d.]+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\S.*)$')
LLM_RE = re.compile(
    r'^\s*llm_(\w+)\s+n=\s*(\d+)\s+mean=\s*([\d.]+)s\s+stdev=\s*([\d.]+)s\s+min=\s*([\d.]+)s\s+max=\s*([\d.]+)s')


def wilson_ci(k, n, z=1.959964):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (centre - half, centre + half)


def parse(path):
    d = {'wall': [], 'l2': [], 'edges': [], 'l1': [], 'gf': [],
         'impact_fail': 0, 'citation_fail': 0, 'llm': {}, 'model': '?'}
    for line in open(path):
        m = re.search(r'Model:\s*([\w.:]+)\s*[—-]\s*Runs', line)
        if m:
            d['model'] = m.group(1)
        m = RUN_RE.match(line)
        if m:
            # contaminated run (overlapping events from an aborted batch)
            if int(m.group(3)) > 35:
                print(f"  [{path}] dropping contaminated run {m.group(1)} "
                      f"(L2 events={m.group(3)})")
                continue
            d['wall'].append(float(m.group(2)))
            d['l2'].append(int(m.group(3)))
            d['edges'].append(int(m.group(4)))
            d['l1'].append(int(m.group(5)))
            d['gf'].append(int(m.group(6)))
            agents = m.group(7).strip()
            if 'impact_agent' in agents:
                d['impact_fail'] += 1
            if 'citation_agent' in agents:
                d['citation_fail'] += 1
        m = LLM_RE.match(line)
        if m:
            d['llm'][m.group(1)] = dict(
                n=int(m.group(2)), mean=float(m.group(3)), stdev=float(m.group(4)),
                min=float(m.group(5)), max=float(m.group(6)))
    d['n'] = len(d['wall'])
    return d


def report(d):
    n = d['n']
    print(f"=== {d['model']} (N={n}) ===")
    w = d['wall']
    print(f"  Wall-clock: mean={statistics.mean(w):.2f}s stdev={statistics.stdev(w):.2f}s "
          f"min={min(w):.2f}s max={max(w):.2f}s")
    print(f"  L2 events/run:  mean={statistics.mean(d['l2']):.2f}")
    print(f"  Edges/run:      mean={statistics.mean(d['edges']):.2f}")
    print(f"  L1 records/run: mean={statistics.mean(d['l1']):.2f} "
          f"(all=15: {all(x == 15 for x in d['l1'])})")
    k = d['impact_fail']
    print(f"  impact GOAL_FAILED:   {k}/{n} = {k/n*100:.0f}%")
    k = d['citation_fail']
    lo, hi = wilson_ci(k, n)
    print(f"  citation GOAL_FAILED: {k}/{n} = {k/n*100:.0f}%  "
          f"Wilson 95% CI [{lo*100:.1f}%–{hi*100:.1f}%]")
    mean_events = statistics.mean(d['l2'])
    overhead_pct = (mean_events * 5 / 1000) / statistics.mean(w) * 100
    print(f"  Overhead estimate: {mean_events:.0f} events × 5ms = "
          f"{mean_events*5:.0f}ms = {overhead_pct:.2f}% of wall-clock")
    print(f"  LLM latency per agent:")
    tot = sum(v['mean'] for v in d['llm'].values())
    for a in AGENTS:
        v = d['llm'].get(a)
        if v:
            print(f"    {a:18} n={v['n']:3} mean={v['mean']:6.2f}s "
                  f"stdev={v['stdev']:5.2f}s  ({v['mean']/tot*100:.0f}% of LLM total {tot:.1f}s)")
    print()


def make_figures(dt, dm):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family': 'serif', 'font.size': 13})

    # ── fig4: stacked failure bars ───────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(6.0, 4.2))
    labels = ['impact\n(tiny)', 'impact\n(mistral)', 'citation\n(tiny)', 'citation\n(mistral)']
    silent = [100, 100, 0, 0]
    stoch = [0, 0, dt['citation_fail']/dt['n']*100, dm['citation_fail']/dm['n']*100]
    clean = [100 - s - st for s, st in zip(silent, stoch)]
    x = range(4)
    ax.bar(x, clean, 0.55, color='#3a7d2c', label='Clean')
    ax.bar(x, stoch, 0.55, bottom=clean, color='#c8821a', label='Stochastic L1 error')
    ax.bar(x, silent, 0.55, bottom=[c+s for c, s in zip(clean, stoch)],
           color='#b03a3a', label='Silent (L1=FIN, L2=FAIL)')
    for i in (0, 1):
        ax.text(i, 50, '100%', ha='center', color='white', fontweight='bold', fontsize=14)
    for i, d in ((2, dt), (3, dm)):
        pct = d['citation_fail']/d['n']*100
        if pct > 5:
            ax.text(i, 100 - pct/2, f'{pct:.0f}%', ha='center', color='white',
                    fontweight='bold', fontsize=14)
        # Wilson 95% CI on the citation failure rate, drawn on the
        # clean/failed boundary (y = 100 - rate)
        lo, hi = wilson_ci(d['citation_fail'], d['n'])
        lo *= 100; hi *= 100
        ax.errorbar(i, 100 - pct, yerr=[[hi - pct], [pct - lo]],
                    fmt='none', ecolor='black', elinewidth=1.4, capsize=5)
    ax.set_xticks(list(x)); ax.set_xticklabels(labels)
    ax.set_ylabel('Runs (%)'); ax.set_ylim(0, 118)
    ax.legend(loc='upper right', fontsize=10, framealpha=0.95)
    ax.spines[['top', 'right']].set_visible(False)
    ax.grid(axis='y', alpha=0.3)
    ax.set_axisbelow(True)
    fig.tight_layout()
    out = os.path.join(FIG_DIR, 'fig4_failures.pdf')
    fig.savefig(out); plt.close(fig)
    print(f"wrote {out}")

    # ── fig6: LLM latency grouped bars ───────────────────────────────────────
    fig, ax = plt.subplots(figsize=(6.0, 4.2))
    names = [a.replace('_agent', '') for a in AGENTS]
    lt = [dt['llm'][a]['mean'] for a in AGENTS]
    lm = [dm['llm'][a]['mean'] for a in AGENTS]
    x = range(5); w = 0.38
    ax.bar([i - w/2 for i in x], lt, w, color='#4292d6', label='tinyllama 1.1B')
    ax.bar([i + w/2 for i in x], lm, w, color='#4b4bb5', label='Mistral 7B')
    # annotate the dominant agent of each model
    pct_t = lt[3] / statistics.mean(dt['wall']) * 100
    ax.annotate(f'{lt[3]:.1f}s ({pct_t:.0f}%)', xy=(3 - w/2, lt[3]),
                xytext=(2.2, lt[3] + 14), color='#1a5e96', fontsize=11,
                arrowprops=dict(arrowstyle='->', color='#1a5e96'), ha='center')
    pct_m = lm[1] / statistics.mean(dm['wall']) * 100
    ax.annotate(f'{lm[1]:.1f}s ({pct_m:.0f}%)', xy=(1 + w/2, lm[1]),
                xytext=(1.9, lm[1] + 6), color='#b03a3a', fontsize=11,
                arrowprops=dict(arrowstyle='->', color='#b03a3a'), ha='center')
    ax.set_xticks(list(x)); ax.set_xticklabels(names, rotation=20)
    ax.set_ylabel('Mean LLM latency (s)')
    ax.legend(loc='upper left', fontsize=11, framealpha=0.95)
    ax.spines[['top', 'right']].set_visible(False)
    ax.grid(axis='y', alpha=0.3)
    ax.set_axisbelow(True)
    fig.tight_layout()
    out = os.path.join(FIG_DIR, 'fig6_latency.pdf')
    fig.savefig(out); plt.close(fig)
    print(f"wrote {out}")


# Mistral LLM latencies recomputed from MongoDB excluding the contaminated
# run-1 workflow (ea4fb94e) and including the replacement run (abb6e074).
# The summary block inside results_mistral_50.txt was computed before the
# replacement run existed, so it is overridden here. n=50 per agent.
MISTRAL_LLM_CLEAN = {
    'ingestion_agent': dict(n=50, mean=20.67, stdev=3.42),
    'analysis_agent':  dict(n=50, mean=75.41, stdev=6.39),
    'impact_agent':    dict(n=50, mean=30.04, stdev=2.54),
    'citation_agent':  dict(n=50, mean=52.79, stdev=6.61),
    'synthesis_agent': dict(n=50, mean=39.88, stdev=4.62),
}

MODEL_LABELS = {
    'tinyllama': 'tinyllama 1.1B', 'mistral': 'Mistral 7B',
    'llama3.1:8b': 'Llama 3.1 8B', 'qwen2.5:32b': 'Qwen 2.5 32B',
}
LADDER_COLORS = ['#4292d6', '#4b4bb5', '#7a3fa8', '#b03a6e']


def make_ladder_figures(ds):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family': 'serif', 'font.size': 12})
    labels = [MODEL_LABELS.get(d['model'], d['model']) for d in ds]
    short = [l.split()[0].replace('tinyllama', 'tiny') for l in labels]
    nm = len(ds)

    # ── fig7: failure rates per agent across the model ladder ───────────────
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    xs_imp = list(range(nm))
    xs_cit = [nm + 1 + i for i in range(nm)]
    ax.bar(xs_imp, [100]*nm, 0.7, color='#b03a3a', label='Silent (L1=FIN, L2=FAIL)')
    for x, d in zip(xs_imp, ds):
        ax.text(x, 50, '100%', ha='center', color='white', fontweight='bold',
                fontsize=11, rotation=90)
    for x, d in zip(xs_cit, ds):
        pct = d['citation_fail']/d['n']*100
        ax.bar(x, 100-pct, 0.7, color='#3a7d2c',
               label='Clean' if x == xs_cit[0] else None)
        ax.bar(x, pct, 0.7, bottom=100-pct, color='#c8821a',
               label='Stochastic L1 error' if x == xs_cit[0] else None)
        lo, hi = wilson_ci(d['citation_fail'], d['n'])
        ax.errorbar(x, 100-pct, yerr=[[hi*100-pct], [pct-lo*100]],
                    fmt='none', ecolor='black', elinewidth=1.2, capsize=4)
        ax.text(x, 100-pct/2, f'{pct:.0f}%', ha='center', color='white',
                fontweight='bold', fontsize=10)
    ax.axhline(70, xmin=(nm+0.7)/(2*nm+1), color='black', ls=':', lw=1)
    ax.text(xs_cit[-1]+0.55, 69, '30% design\nprobability', fontsize=8, va='top')
    ax.set_xticks(xs_imp + xs_cit)
    ax.set_xticklabels([f'impact\n({s})' for s in short] +
                       [f'citation\n({s})' for s in short], fontsize=8.5)
    ax.set_ylabel('Runs (%)'); ax.set_ylim(0, 119)
    ax.legend(loc='upper left', fontsize=8.5, framealpha=0.95, ncol=3)
    ax.spines[['top', 'right']].set_visible(False)
    ax.grid(axis='y', alpha=0.3); ax.set_axisbelow(True)
    fig.tight_layout()
    out = os.path.join(FIG_DIR, 'fig7_failures_robopaas.pdf')
    fig.savefig(out); plt.close(fig)
    print(f"wrote {out}")

    # ── fig8: per-agent LLM latency across the ladder ────────────────────────
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    names = [a.replace('_agent', '') for a in AGENTS]
    w = 0.8 / nm
    for j, (d, lab) in enumerate(zip(ds, labels)):
        vals = [d['llm'][a]['mean'] for a in AGENTS]
        ax.bar([i - 0.4 + w/2 + j*w for i in range(5)], vals, w,
               color=LADDER_COLORS[j % len(LADDER_COLORS)], label=lab)
    ax.set_xticks(range(5)); ax.set_xticklabels(names, rotation=15)
    ax.set_ylabel('Mean LLM latency (s)')
    ax.legend(fontsize=9, framealpha=0.95)
    ax.spines[['top', 'right']].set_visible(False)
    ax.grid(axis='y', alpha=0.3); ax.set_axisbelow(True)
    fig.tight_layout()
    out = os.path.join(FIG_DIR, 'fig8_latency_robopaas.pdf')
    fig.savefig(out); plt.close(fig)
    print(f"wrote {out}")


if __name__ == '__main__':
    no_fig = '--no-figures' in sys.argv
    ladder = '--ladder' in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith('--')]

    if ladder:
        ds = [parse(f) for f in args]
        for d in ds:
            report(d)
        print(f"{'Model':16} {'wall mean':>10} {'silent':>8} {'citation':>9}  95% CI")
        for d in ds:
            lo, hi = wilson_ci(d['citation_fail'], d['n'])
            print(f"{d['model']:16} {statistics.mean(d['wall']):9.2f}s "
                  f"{d['impact_fail']}/{d['n']:>3} {d['citation_fail']}/{d['n']:>4}   "
                  f"[{lo*100:.1f}%–{hi*100:.1f}%]")
        if not no_fig:
            make_ladder_figures(ds)
        sys.exit(0)

    dt, dm = parse(args[0]), parse(args[1])
    # CPU-dataset correction — applies only to the LOCAL mistral results file
    # (see MISTRAL_LLM_CLEAN comment); never to robopaas data.
    if dm['model'] == 'mistral' and 'robopaas' not in args[1]:
        for a, v in MISTRAL_LLM_CLEAN.items():
            dm['llm'][a].update(v)
    report(dt)
    report(dm)
    # pooled citation rate
    k = dt['citation_fail'] + dm['citation_fail']
    n = dt['n'] + dm['n']
    lo, hi = wilson_ci(k, n)
    print(f"Pooled citation failure: {k}/{n} = {k/n*100:.1f}%  CI [{lo*100:.1f}%–{hi*100:.1f}%]")
    if not no_fig:
        make_figures(dt, dm)
