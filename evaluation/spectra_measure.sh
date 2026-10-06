#!/bin/bash
# =============================================================================
# SPECTRA Performance Measurement Script — Model Comparison Edition
# Runs N pipeline executions for a given model and stores results tagged
# by model name in MongoDB for clean cross-model comparison.
#
# USAGE:
#   chmod +x spectra_measure.sh
#
#   # Run for tinyllama (fast — ~10 min for 10 runs):
#   ./spectra_measure.sh tinyllama 10 2>&1 | tee results/local/results_tinyllama_50.txt
#
#   # Switch model, then run for mistral (slower — ~20 min for 10 runs):
#   kubectl set env deployment/spectra-pipeline -n openlit-demo OLLAMA_MODEL=mistral
#   kubectl rollout restart deployment/spectra-pipeline -n openlit-demo
#   kubectl rollout status deployment/spectra-pipeline -n openlit-demo
#   ./spectra_measure.sh mistral 10 2>&1 | tee results/local/results_mistral_50.txt
#
#   # Paste BOTH result files back to Claude.
#
# REQUIREMENTS:
#   - kubectl configured, openlit-demo namespace reachable
#   - Port-forward running: kubectl port-forward svc/spectra-pipeline -n openlit-demo 8502:8000
#   - MongoDB reachable via kubectl exec
# =============================================================================

MODEL=${1:-tinyllama}
N_RUNS=${2:-10}
# Override for remote clusters, e.g.:
#   KUBECONFIG=~/.kube/robopaas-config.yaml PIPELINE_URL=http://localhost:8503 \
#     ./spectra_measure.sh mistral 50
PIPELINE_URL=${PIPELINE_URL:-http://localhost:8502}
MONGO="kubectl exec -n openlit-demo deploy/flowcept-mongodb -- mongosh flowcept --quiet --eval"
DOCUMENT="observability for LLM-based multi-agent systems using dual-layer provenance"

echo "============================================================"
echo "SPECTRA Measurement — Model: $MODEL — Runs: $N_RUNS"
echo "Started: $(date)"
echo "============================================================"
echo ""

# ── Capture measurement environment (provenance for the measurements) ────────
echo "[env] Measurement environment:"
echo "  Kube context:   $(kubectl config current-context 2>/dev/null || echo unknown)"
OLLAMA_NODE=$(kubectl get pod -n openlit-demo -l app=ollama -o jsonpath='{.items[0].spec.nodeName}' 2>/dev/null || echo unknown)
echo "  Ollama node:    $OLLAMA_NODE"
GPU_INFO=$(kubectl exec -n openlit-demo deploy/ollama -- nvidia-smi --query-gpu=name,memory.used,utilization.gpu --format=csv,noheader 2>/dev/null || echo "none (CPU inference)")
echo "  Ollama GPU:     $GPU_INFO"
echo "  Ollama limits:  $(kubectl get deploy ollama -n openlit-demo -o jsonpath='{.spec.template.spec.containers[0].resources.limits}' 2>/dev/null || echo unknown)"
echo "  Ollama models:  $(kubectl exec -n openlit-demo deploy/ollama -- ollama list 2>/dev/null | tail -n +2 | awk '{print $1}' | paste -sd' ' || echo unknown)"
if [ "$GPU_INFO" = "none (CPU inference)" ]; then
  # CPU inference: the client host matters only for single-node local clusters,
  # but governor/load are decisive there — record them.
  echo "  CPU governor:   $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || echo unknown)"
  echo "  CPU threads:    $(nproc 2>/dev/null || echo unknown)"
  echo "  Load average:   $(cut -d' ' -f1-3 /proc/loadavg 2>/dev/null || echo unknown)"
fi
echo ""

# ── Verify model is active ───────────────────────────────────────────────────
# IMPORTANT: check the LIVE pod, not the deployment spec — the spec updates the
# instant `kubectl set env` runs, even while the old pod is still serving.
echo "[setup] Checking pipeline reports model=$MODEL..."
HEALTH=$(curl -s "${PIPELINE_URL}/health" 2>/dev/null || echo "{}")
echo "  Health: $HEALTH"

if ! kubectl rollout status deployment/spectra-pipeline -n openlit-demo --timeout=5s > /dev/null 2>&1; then
  echo "  ERROR: spectra-pipeline rollout is still in progress — the old pod may"
  echo "         still be serving. Wait for it to finish, then re-run:"
  echo "         kubectl rollout status deployment/spectra-pipeline -n openlit-demo"
  exit 1
fi

LIVE_POD=$(kubectl get pods -n openlit-demo --no-headers 2>/dev/null \
  | grep spectra-pipeline | grep Running | awk '{print $1}' | head -1)
ACTIVE_MODEL=$(kubectl get pod "$LIVE_POD" -n openlit-demo \
  -o jsonpath='{.spec.containers[?(@.name=="spectra-pipeline")].env[?(@.name=="OLLAMA_MODEL")].value}' 2>/dev/null || echo "unknown")
echo "  Live pod: $LIVE_POD"
echo "  OLLAMA_MODEL (live pod): $ACTIVE_MODEL"

if [ "$ACTIVE_MODEL" != "$MODEL" ]; then
  echo ""
  echo "  WARNING: Live pod model '$ACTIVE_MODEL' does not match requested '$MODEL'."
  echo "  To switch: kubectl set env deployment/spectra-pipeline -n openlit-demo OLLAMA_MODEL=$MODEL"
  echo "             kubectl rollout restart deployment/spectra-pipeline -n openlit-demo"
  echo "             kubectl rollout status deployment/spectra-pipeline -n openlit-demo"
  echo ""
  if [ -t 0 ]; then
    read -p "  Continue anyway? (y/N): " CONT
    [ "$CONT" != "y" ] && exit 1
  else
    exit 1
  fi
fi

# ── Clear model-specific data from previous runs ─────────────────────────────
echo ""
echo "[setup] Clearing previous data for model=$MODEL..."
$MONGO "db.tasks.deleteMany({'custom_metadata.model':'$MODEL'})" > /dev/null
echo "  Done."
echo ""

# ── Main measurement loop ─────────────────────────────────────────────────────
echo "Run | Wall(s) | L2 events | Edges | L1 records | GOAL_FAILED | Failed agents"
echo "----|---------|-----------|-------|------------|-------------|---------------"

declare -a WALL_TIMES
declare -a L2_EVENT_COUNTS
declare -a EDGE_COUNTS
declare -a L1_COUNTS
declare -a GF_COUNTS
IMPACT_FAILS=0
CITATION_FAILS=0

for i in $(seq 1 $N_RUNS); do
    START_MS=$(date +%s%3N)

    RESPONSE=$(curl -s "${PIPELINE_URL}/run?document=${DOCUMENT// /+}" 2>/dev/null)

    END_MS=$(date +%s%3N)
    WALL_MS=$((END_MS - START_MS))
    WALL_S=$(echo "scale=2; $WALL_MS / 1000" | bc)

    # Parse summary from response JSON
    L2E=$(echo "$RESPONSE" | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
    print(d.get('summary',{}).get('layer2_events','?'))
except: print('?')
" 2>/dev/null)

    L2G=$(echo "$RESPONSE" | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
    print(d.get('summary',{}).get('causal_edges','?'))
except: print('?')
" 2>/dev/null)

    L1R=$(echo "$RESPONSE" | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
    print(d.get('summary',{}).get('layer1_records','?'))
except: print('?')
" 2>/dev/null)

    GF=$(echo "$RESPONSE" | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
    print(d.get('summary',{}).get('goal_failed','?'))
except: print('?')
" 2>/dev/null)

    FA=$(echo "$RESPONSE" | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
    agents=d.get('summary',{}).get('goal_failed_agents',[])
    print(','.join(agents) if agents else 'none')
except: print('?')
" 2>/dev/null)

    # Tag all records from this run with the model name
    # (The pipeline sets custom_metadata.framework='spectra' — we add model tag here)
    RUN_ID=$(echo "$RESPONSE" | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
    print(d.get('run_id',''))
except: print('')
" 2>/dev/null)

    if [ -n "$RUN_ID" ]; then
        $MONGO "db.tasks.updateMany(
            {workflow_id:'$RUN_ID'},
            {'\$set':{'custom_metadata.model':'$MODEL'}}
        )" > /dev/null 2>&1
    fi

    printf "%3d | %7.2f | %9s | %5s | %10s | %11s | %s\n" \
        $i $WALL_S "$L2E" "$L2G" "$L1R" "$GF" "$FA"

    # After the first run, verify Ollama actually served the requested model.
    # This catches the case where the env var says one thing but inference ran
    # on a previously loaded model. Abort ONLY on positive evidence of a
    # different model — an empty response usually means a transient kubectl
    # exec failure (or the model already unloaded), not a wrong model.
    if [ $i -eq 1 ]; then
        LOADED=""
        for try in 1 2 3; do
            LOADED=$(kubectl exec -n openlit-demo deploy/ollama -- ollama ps 2>/dev/null | tail -n +2 | awk '{print $1}')
            [ -n "$LOADED" ] && break
            sleep 5
        done
        if [ -z "$LOADED" ]; then
            echo "    [WARNING: could not read 'ollama ps' after 3 tries — model not verified]"
        elif echo "$LOADED" | grep -q "^${MODEL}"; then
            echo "    [verified: ollama ps shows ${MODEL} loaded]"
        else
            echo ""
            echo "  ERROR: After run 1, 'ollama ps' shows loaded model(s): ${LOADED}"
            echo "         — not the requested '$MODEL'. Inference is NOT running on"
            echo "         the model you think. Aborting before wasting $N_RUNS runs."
            exit 1
        fi

        # Cross-cluster check: the run served via PIPELINE_URL must exist in
        # THIS kubeconfig's MongoDB. If not, the port-forward points at a
        # different cluster than kubectl does (e.g. a stale or foreign
        # port-forward grabbed the port) and every number would be garbage.
        if [ -n "$RUN_ID" ]; then
            FOUND=$($MONGO "db.tasks.countDocuments({workflow_id:'$RUN_ID'})" 2>/dev/null | tail -1)
            if [ "${FOUND:-0}" -eq 0 ] 2>/dev/null; then
                echo ""
                echo "  ERROR: Run 1 (workflow_id=$RUN_ID) is NOT in this cluster's MongoDB."
                echo "         PIPELINE_URL ($PIPELINE_URL) is serving a DIFFERENT cluster"
                echo "         than the current kubeconfig. Check which kubectl started the"
                echo "         port-forward on that port. Aborting."
                exit 1
            fi
            echo "    [verified: run 1 records present in target cluster MongoDB]"
        fi
    fi

    case "$FA" in *impact_agent*) IMPACT_FAILS=$((IMPACT_FAILS+1));; esac
    case "$FA" in *citation_agent*) CITATION_FAILS=$((CITATION_FAILS+1));; esac

    WALL_TIMES+=($WALL_S)
    L2_EVENT_COUNTS+=($L2E)
    EDGE_COUNTS+=($L2G)
    L1_COUNTS+=($L1R)
    GF_COUNTS+=($GF)

    sleep 3
done

echo ""
echo "============================================================"
echo "SUMMARY STATISTICS"
echo "============================================================"
echo ""

# ── Python statistics block ──────────────────────────────────────────────────
python3 - "$MODEL" "$N_RUNS" "$IMPACT_FAILS" "$CITATION_FAILS" << 'PYEOF'
import sys, json, statistics, subprocess, functools

# stream output through tee instead of block-buffering (looks frozen otherwise)
print = functools.partial(print, flush=True)

MODEL = sys.argv[1]
N = int(sys.argv[2])
IMPACT_FAILS_LOG = int(sys.argv[3])    # impact GOAL_FAILED count from run log (authoritative L2)
CITATION_FAILS_LOG = int(sys.argv[4])  # citation GOAL_FAILED count from run log

def mongo(query):
    r = subprocess.run(
        ['kubectl','exec','-n','openlit-demo','deploy/flowcept-mongodb','--',
         'mongosh','flowcept','--quiet','--eval', query],
        capture_output=True, text=True, timeout=30
    )
    return r.stdout.strip()

print(f"Model: {MODEL}")
print(f"Runs requested: {N}")
print("")

# ── Per-run analysis — ONE mongosh round-trip for all runs ───────────────────
# (A per-run kubectl exec loop costs ~3s × 4 queries × N runs through a remote
#  cluster proxy; iterating server-side keeps the stats phase under a minute.)
# impact_agent note: the FINISHED record carries generated.fallback=true when
# the tool failed and the fallback ran. The RUNNING record of the same agent
# has an empty `generated` — a findOne without status filter can return it and
# silently miss the flag, which is what broke earlier versions of this query.
per_run_raw = mongo(f"""
var out = [];
[...new Set(db.tasks.find({{'custom_metadata.model':'{MODEL}'}}).map(d=>d.workflow_id).toArray())].forEach(rid => {{
  out.push({{
    rid: rid,
    l1_total: db.tasks.countDocuments({{workflow_id:rid,'custom_metadata.model':'{MODEL}'}}),
    l1_fin:   db.tasks.countDocuments({{workflow_id:rid,subtype:'agent_task',status:'FINISHED'}}),
    imp_fb:   db.tasks.countDocuments({{workflow_id:rid,activity_id:'impact_agent',subtype:'agent_task',status:'FINISHED','generated.fallback':true}}),
    imp_fin:  db.tasks.countDocuments({{workflow_id:rid,activity_id:'impact_agent',subtype:'agent_task',status:'FINISHED'}}),
    cit_err:  db.tasks.countDocuments({{workflow_id:rid,activity_id:'citation_agent',subtype:'agent_task',status:'ERROR'}})
  }});
}});
print(JSON.stringify(out));
""")
try:
    per_run = json.loads(per_run_raw.split('\n')[-1])
except Exception:
    per_run = []
runs = [r['rid'] for r in per_run]
print(f"Unique run IDs found in MongoDB: {len(runs)}")
print("")

l1_per_run        = []
impact_failed     = 0
citation_failed   = 0
all_finished_runs = 0
impact_silent     = 0   # impact: fallback=true in L2 AND status=FINISHED in L1

print("Per-run breakdown:")
print(f"{'Run ID':12} {'L1 records':10} {'L1 FINISHED':11} {'impact fallback':15} {'citation err':12}")
print("-"*65)

for r in per_run:
    l1_per_run.append(r['l1_fin'])
    imp_fallback = r['imp_fb'] > 0
    cit_err      = r['cit_err'] > 0
    if imp_fallback: impact_failed += 1
    if imp_fallback and r['imp_fin'] > 0: impact_silent += 1
    if cit_err: citation_failed += 1
    if r['l1_fin'] >= 5: all_finished_runs += 1
    print(f"{r['rid'][:12]:12} {r['l1_total']:10} {r['l1_fin']:11} {'YES' if imp_fallback else 'no':15} {'YES' if cit_err else 'no':12}")

n = len(runs)
print("")

# ── Silent failure analysis ───────────────────────────────────────────────────
print("="*65)
print("SILENT FAILURE DETECTION (Layer1=FINISHED, Layer2=GOAL_FAILED)")
print("="*65)
print(f"  impact_agent  (deliberate tool failure):")
print(f"    GOAL_FAILED, run log (L2):    {IMPACT_FAILS_LOG}/{N} = {IMPACT_FAILS_LOG/max(N,1)*100:.0f}%")
print(f"    fallback=true, MongoDB (L2):  {impact_failed}/{n} = {impact_failed/max(n,1)*100:.0f}%")
print(f"    FINISHED in Layer 1:          {all_finished_runs}/{n} = {all_finished_runs/max(n,1)*100:.0f}% (all 5 agents)")
print(f"    Silent (impact L1=FINISHED & L2 fallback): {impact_silent}/{n} = {impact_silent/max(n,1)*100:.0f}%  <-- KEY METRIC")
if impact_failed != IMPACT_FAILS_LOG:
    print(f"    WARNING: run log and MongoDB disagree — investigate before using these numbers.")
print(f"  citation_agent (stochastic ~30% failure by design):")
print(f"    GOAL_FAILED, run log (L2):    {CITATION_FAILS_LOG}/{N} = {CITATION_FAILS_LOG/max(N,1)*100:.0f}%")
print(f"    ERROR in Layer 1 (MongoDB):   {citation_failed}/{n} = {citation_failed/max(n,1)*100:.0f}%")
print(f"    (Layer 1 does surface citation errors — not silent)")
print("")

# ── Layer 1 consistency ───────────────────────────────────────────────────────
print("="*65)
print("LAYER 1 CONSISTENCY")
print("="*65)
if l1_per_run:
    print(f"  AgentTool FINISHED per run:")
    print(f"    mean={statistics.mean(l1_per_run):.1f}, "
          f"stdev={statistics.stdev(l1_per_run) if len(l1_per_run)>1 else 0:.2f}, "
          f"min={min(l1_per_run)}, max={max(l1_per_run)}")
print("")

# ── Durations, LLM latency, invocation count, synthesis sample ──────────────
# Single mongosh round-trip for everything remaining (each kubectl exec costs
# ~14s through a remote cluster proxy).
last_rid = runs[-1] if runs else ''
extra_raw = mongo(f"""
var agents = ['ingestion_agent','analysis_agent','impact_agent','citation_agent','synthesis_agent'];
var out = {{agent_durs:{{}}, llm_durs:{{}}, llm_total:0, synthesis:''}};
out.llm_total = db.tasks.countDocuments({{'custom_metadata.model':'{MODEL}',subtype:'llm_task'}});
agents.forEach(a => {{
  out.agent_durs[a] = db.tasks.find({{activity_id:a,subtype:'agent_task',status:'FINISHED','custom_metadata.model':'{MODEL}'}},{{started_at:1,ended_at:1,_id:0}}).toArray();
  out.llm_durs[a]   = db.tasks.find({{activity_id:'llm_'+a,subtype:'llm_task','custom_metadata.model':'{MODEL}'}},{{started_at:1,ended_at:1,_id:0}}).toArray();
}});
var syn = db.tasks.findOne({{workflow_id:'{last_rid}',activity_id:'synthesis_agent',subtype:'agent_task'}},{{generated:1,_id:0}});
if (syn && syn.generated && syn.generated.synthesis) out.synthesis = String(syn.generated.synthesis).substring(0,300);
print(JSON.stringify(out));
""")
try:
    extra = json.loads(extra_raw.split('\n')[-1])
except Exception:
    extra = {'agent_durs': {}, 'llm_durs': {}, 'llm_total': 0, 'synthesis': ''}

def dur_stats(recs):
    return [r['ended_at']-r['started_at'] for r in recs
            if 'started_at' in r and 'ended_at' in r and r['ended_at'] > r['started_at']]

print(f"  LLM invocations: {extra['llm_total']} total = {extra['llm_total']/max(n,1):.1f} per run")
print("")

for title, key, pad in (("AGENT DURATION (Layer 1 FINISHED records, seconds)", 'agent_durs', 20),
                        ("LLM CALL LATENCY (llm_task records, seconds)",      'llm_durs',   26)):
    print("="*65)
    print(title)
    print("="*65)
    for agent in ['ingestion_agent','analysis_agent','impact_agent','citation_agent','synthesis_agent']:
        label = agent if key == 'agent_durs' else f"llm_{agent}"
        durs = dur_stats(extra[key].get(agent, []))
        if durs:
            print(f"  {label:{pad}} n={len(durs):3}  "
                  f"mean={statistics.mean(durs):6.2f}s  "
                  f"stdev={statistics.stdev(durs) if len(durs)>1 else 0:5.2f}s  "
                  f"min={min(durs):5.2f}s  max={max(durs):6.2f}s")
        else:
            print(f"  {label:{pad}} no data")
    print("")

print("="*65)
print("SAMPLE OUTPUT QUALITY (last run synthesis)")
print("="*65)
print(f"  synthesis (first 300 chars):")
print(f"  {extra['synthesis'] or '(not found)'}")
print("")

print("="*65)
print(f"MEASUREMENT COMPLETE — Model: {MODEL}")
print("="*65)
PYEOF

echo ""
echo "Finished: $(date)"
