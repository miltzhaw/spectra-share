# Multi-Agent Observability Demo — Setup & Usage Guide

## What this stack observes

```
agent-a → agent-b → agent-c
   └──────────────────────→ otel-collector (OTLP :4318)
                                   │
          ┌────────────┬───────────┼──────────────┬──────────────┐
          ▼            ▼           ▼              ▼              ▼
       OpenLIT       Jaeger    Prometheus      Langfuse       Phoenix
       :3000        :16686      :9090           :3002          :6006
     (AI view)   (waterfalls)  (metrics)   (evals/prompts) (embeddings
                                   │                         RAG eval)
                               Grafana
                                :3001
                           (unified dashboard)

   Flowcept consumer ← Redis ← agents (@flowcept_task)
          ↓
       MongoDB → flowcept-rest :5000 → Flowcept UI :8020
                              → flowcept-agent :8095 (NL queries)
```

---

## Prerequisites

- `kubectl` and `helm ≥ 3.12` installed
- A running Kubernetes cluster (minikube / kind / k3s all work)
- OpenLIT already installed in namespace `openlit`:

```bash
helm repo add openlit https://openlit.github.io/helm/
helm install openlit openlit/openlit -n openlit --create-namespace
```

---

## Step 1 — Deploy Langfuse

```bash
helm repo add langfuse https://langfuse.github.io/langfuse-k8s
helm repo update
helm install langfuse langfuse/langfuse \
  --namespace langfuse --create-namespace \
  --set langfuse.salt.value="change-me-to-32-random-chars-xx" \
  --set langfuse.nextauth.secret.value="change-me-to-32-random-chars-yy" \
  --set langfuse.nextauth.url="http://localhost:3002" \
  --set postgresql.auth.password="${DB_PASSWORD}" \
  --set clickhouse.auth.password="${DB_PASSWORD}" \
  --set redis.auth.password="${DB_PASSWORD}"

Or with the file:

helm install langfuse/upgrade langfuse/langfuse   --namespace langfuse   -f langfuse-storage-values.yaml 


kubectl get pods -n langfuse -w
```

---

## Step 2 — Get Langfuse API keys

```bash
kubectl port-forward svc/langfuse-web -n langfuse 3002:3000
```

Open **http://localhost:3002** → sign up → **Settings → API Keys → Create**

```bash
echo -n "pk-lf-YOUR-KEY:sk-lf-YOUR-SECRET" | base64 -w 0
```

Edit the ConfigMap and replace `LANGFUSE_BASE64_ENCODED_KEY_HERE`:

```bash
kubectl edit configmap otel-collector-config -n openlit-demo
kubectl rollout restart deploy/otel-collector -n openlit-demo
```

---

## Step 3 — Apply the demo deployment

```bash
# Create the Ollama-compatible secret
kubectl create secret generic openai-secret \
  --namespace openlit-demo \
  --from-literal=api-key=ollama

kubectl delete namespace openlit-demo 2>/dev/null; sleep 5
kubectl apply -f demo_test.yaml

# Watch pods — agents take ~90s for pip installs
kubectl get pods -n openlit-demo -w

# Pull tinyllama into Ollama
kubectl wait --for=condition=ready pod -l app=ollama -n openlit-demo --timeout=120s
kubectl exec -n openlit-demo deploy/ollama -- ollama pull tinyllama
```

---

## Step 4 — Open port-forwards

Run each in a **separate terminal**:

```bash
kubectl port-forward svc/agent-a         -n openlit-demo 8080:8000
kubectl port-forward svc/jaeger          -n openlit-demo 16686:16686
kubectl port-forward svc/prometheus      -n openlit-demo 9090:9090
kubectl port-forward svc/grafana         -n openlit-demo 3001:3000
kubectl port-forward svc/phoenix         -n openlit-demo 6006:6006
kubectl port-forward svc/flowcept-ui     -n openlit-demo 8020:8020
kubectl port-forward svc/flowcept-agent  -n openlit-demo 8010:8095
kubectl port-forward svc/flowcept-rest   -n openlit-demo 5000:5000
kubectl port-forward svc/openlit         -n openlit      3000:3000
kubectl port-forward svc/langfuse-web    -n langfuse     3002:3000
```

---

## Step 5 — Trigger traces

```bash
# Single trace
curl http://localhost:8080/run

# 10 traces
for i in $(seq 1 10); do curl -s http://localhost:8080/run; sleep 2; done
```

---

## Step 6 — Observe in each tool

### OpenLIT — http://localhost:3000
**Best for: LLM cost attribution, token counts, AI-specific metrics**

- **Requests** → filter by application `multi-agent-demo`
- See token usage (input + output), estimated cost, model name per request
- **Metrics** → aggregated LLM usage dashboards

### Jaeger — http://localhost:16686
**Best for: distributed trace waterfalls, inter-agent latency**

- Service dropdown → select `agent-a` → **Find Traces**
- Click any trace → full **A → B → C waterfall** with span durations
- TraceQL: `{ .service.name="agent-b" && duration > 100ms }`
- TraceQL: `{ status = error }` — find all error traces

### Grafana — http://localhost:3001 (admin / admin)
**Best for: time-series trends, throughput per backend, alerting**

- **Dashboards → Multi-Agent Demo → Multi-Agent Demo Overview**
- Top row: spans sent to each backend (OpenLIT, Jaeger, Phoenix, Langfuse)
- Middle row: span rate over time
- **Explore → Jaeger datasource** — trace search inline

### Prometheus — http://localhost:9090
**Best for: raw metric queries, verifying collector health**

```promql
rate(otelcol_exporter_sent_spans_total[1m])     # spans/s per exporter
sum(otelcol_exporter_send_failed_spans_total)    # export failures (should be 0)
otelcol_receiver_accepted_spans_total            # spans received
```

Check **Status → Targets** — all scrape targets should be UP.

### Arize Phoenix — http://localhost:6006
**Best for: embedding clusters, RAG evaluation, hallucination detection, prompt playground**

- **Traces** → full trace list with LLM-aware span detail
- **Spans** → filter by span kind, model, latency
- **Embeddings** → input/output embeddings in 2D/3D cluster view — spot distribution drift
- **Datasets & Experiments** → run hallucination, Q&A correctness, toxicity evals (free)
- **Prompt Playground** → test prompts against different models side-by-side (free)

**What Phoenix uniquely shows vs the other tools:**

| Capability | Phoenix | Langfuse | OpenLIT | Jaeger |
|---|---|---|---|---|
| Embedding clusters | ✅ | ❌ | ❌ | ❌ |
| RAG retrieval eval | ✅ | ⚠️ | ❌ | ❌ |
| Hallucination detect | ✅ free | ✅ paid | ❌ | ❌ |
| Prompt playground | ✅ free | ✅ paid | ❌ | ❌ |
| Trace waterfall | ✅ | ✅ | ⚠️ | ✅ Best |
| Self-host complexity | Minimal | High | Low | Low |

### Langfuse — http://localhost:3002
**Best for: prompt versioning, LLM output quality evaluation**

- **Traces** → each agent call with full Input/Output captured
- **Evaluations** → LLM-as-a-judge: Hallucination, Correctness, Toxicity
- **Prompt Management** → version and deploy prompt templates

### Flowcept Provenance UI — http://localhost:8020
**Best for: W3C PROV data lineage, task input/output, reproducibility**

- Connect REST API: `http://localhost:5000/api/v1`
- Connect PROV-AGENT: `http://localhost:8010`
- **Tasks** → live task list with duration and status
- **Prov graph** → lineage graph grouped by agent with cross-agent arrows
- **PROV-AGENT chat** → ask questions about your workflow:

```bash
curl -s -X POST http://localhost:8010/query \
     -H "Content-Type: application/json" \
     -d '{"question": "which agent took longest on average?"}'

curl -s -X POST http://localhost:8010/query \
     -H "Content-Type: application/json" \
     -d '{"question": "show failed tasks"}'
```

---

## Tool comparison

| Capability | OpenLIT | Jaeger | Grafana | Langfuse | Phoenix | Flowcept |
|---|---|---|---|---|---|---|
| Cross-agent trace | ⚠️ | ✅ Best | ✅ via Jaeger | ✅ | ✅ | ❌ |
| Token counts | ✅ Best | ❌ | ❌ | ✅ | ✅ | ❌ |
| LLM cost | ✅ Best | ❌ | ❌ | ✅ | ✅ | ❌ |
| Latency waterfall | ⚠️ | ✅ Best | ✅ trends | ⚠️ | ✅ | ❌ |
| Embedding clusters | ❌ | ❌ | ❌ | ❌ | ✅ Best | ❌ |
| RAG evaluation | ❌ | ❌ | ❌ | ⚠️ | ✅ Best | ❌ |
| Hallucination detect | ❌ | ❌ | ❌ | ✅ paid | ✅ free | ❌ |
| Prompt playground | ❌ | ❌ | ❌ | ✅ paid | ✅ free | ❌ |
| Prompt versioning | ❌ | ❌ | ❌ | ✅ Best | ❌ | ❌ |
| Data lineage | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ Best |
| Real-time alerting | ❌ | ❌ | ✅ Best | ❌ | ❌ | ❌ |
| W3C PROV standard | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |
| Self-host complexity | Low | Low | Medium | High | Minimal | Medium |

---

## Troubleshooting

### No data in Phoenix?
```bash
kubectl logs -n openlit-demo deploy/otel-collector | grep -E "phoenix|error"
# Should show: Exporting items ... exporter=otlphttp/phoenix
kubectl get pods -n openlit-demo -l app=phoenix
```

### No data in Jaeger?
```bash
kubectl logs -n openlit-demo deploy/otel-collector | grep -E "jaeger|error"
```

### No data in Langfuse?
```bash
kubectl logs -n openlit-demo deploy/otel-collector | grep -E "langfuse|401|403"
# 401 = wrong API key — regenerate base64 and update ConfigMap
```

### Flowcept tasks count stays 0?
```bash
kubectl logs -n openlit-demo deploy/flowcept-consumer --tail=20
kubectl exec -n openlit-demo deploy/flowcept-mongodb -- \
  mongosh flowcept --eval "db.tasks.countDocuments({})"
kubectl exec -n openlit-demo deploy/flowcept-redis -- redis-cli KEYS "*"
```

### Grafana shows no data?
```bash
# Verify Prometheus targets are UP: http://localhost:9090/targets
# Query: otelcol_exporter_sent_spans_total
```

---

## Architecture — data flow

```
agent-a/b/c
    ├── openlit.init()  → otel-collector:4318 (OTLP HTTP)
    └── @flowcept_task  → flowcept-redis:6379 (provenance)

otel-collector (0.104.0)
    ├── otlphttp/openlit  → openlit.openlit.svc.cluster.local:4318
    ├── otlp/jaeger       → jaeger:14317
    ├── otlphttp/phoenix  → phoenix:6006/v1/traces
    ├── otlphttp/langfuse → langfuse-web.langfuse.svc.cluster.local:3000/api/public/otel
    └── prometheus        → exposes :8889 for Prometheus scraping

flowcept-consumer → Redis:6379 → MongoDB:27017
flowcept-rest:5000 + flowcept-agent:8095 → read MongoDB
flowcept-ui:8020 → dashboard UI

prometheus → scrapes otel-collector:8888 + :8889
grafana    → Prometheus + Jaeger datasources
```
