#!/usr/bin/env python3
# =============================================================================
# Combined citation-failure figure across all six experiment arms
# (2 CPU models + 4 GPU models), with Wilson 95% CIs and the 30% design line.
# Regenerates paper/fig_failures_combined.pdf.
#
#   python3 make_combined_failure_fig.py
#
# Rates/CIs are the reported per-model results (see results/local/ and
# results/robopaas/); kept inline so the figure is self-contained.
# =============================================================================
import os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
plt.rcParams.update({'font.family': 'serif', 'font.size': 12})

# (model label, rate%, wilson_lo%, wilson_hi%, platform)
ARMS = [
    ('tiny\n1.1B',  30, 19.1, 43.8, 'cpu'),
    ('Mistral\n7B', 40, 27.6, 53.8, 'cpu'),
    ('tiny\n1.1B',  32, 20.8, 45.8, 'gpu'),
    ('Mistral\n7B', 34, 22.4, 47.8, 'gpu'),
    ('Llama\n8B',   18,  9.8, 30.8, 'gpu'),
    ('Qwen\n32B',   24, 14.3, 37.4, 'gpu'),
]
XS = [0, 1, 2.6, 3.6, 4.6, 5.6]
CPU_C, GPU_C = '#c8821a', '#4b4bb5'
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   '..', 'paper', 'fig_failures_combined.pdf')

fig, ax = plt.subplots(figsize=(6.6, 3.7))
for x, (lab, r, lo, hi, plat) in zip(XS, ARMS):
    ax.bar(x, r, 0.8, color=CPU_C if plat == 'cpu' else GPU_C)
    ax.errorbar(x, r, yerr=[[r - lo], [hi - r]], fmt='none',
                ecolor='black', elinewidth=1.3, capsize=4)
    ax.text(x, hi + 1.5, f'{r}%', ha='center', fontsize=11, fontweight='bold')

ax.axhline(30, color='#b03a3a', ls='--', lw=1.4, zorder=0)
ax.text(5.75, 30.6, '30% design\nprobability', color='#b03a3a',
        fontsize=9, va='bottom', ha='right')

ax.set_xticks(XS)
ax.set_xticklabels([a[0] for a in ARMS], fontsize=10)
ax.set_ylabel('Citation failure rate (%)')
ax.set_ylim(0, 60)
ax.set_xlim(-0.7, 6.3)
ax.text(0.5, -16, 'CPU testbed (2 cores)', ha='center', fontsize=10,
        color=CPU_C, fontweight='bold')
ax.text(4.1, -16, 'GPU cluster (A30)', ha='center', fontsize=10,
        color=GPU_C, fontweight='bold')
ax.axvline(1.8, color='0.8', lw=1, ls=':')
ax.spines[['top', 'right']].set_visible(False)
ax.grid(axis='y', alpha=0.3)
ax.set_axisbelow(True)
fig.tight_layout()
fig.savefig(OUT, bbox_inches='tight')
print('wrote', os.path.abspath(OUT))
