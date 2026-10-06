# SPECTRA Causal DAG vs PROV-AGENT
## Technical Comparison — Differences, Technicalities, and Results
**Leonardo Militano — ZHAW Distributed Systems Group**

---

## 1. Instrumentation Boundary

| | PROV-AGENT | SPECTRA Causal DAG |
|---|---|---|
| **Where it hooks in** | Python function boundary via decorators | Inside the LLM reasoning loop, before any fallback |
| **What triggers a record** | Function entry and exit | Six typed semantic events at reasoning transitions |
| **Timing of failure record** | After the function returns | Before the fallback executes |
| **Code change required** | `@flowcept_agent_tool` decorator | `emit()` calls at six points inside agent logic |

**The critical difference:** PROV-AGENT sees what the function *returned*. SPECTRA sees what the LLM *attempted* before error handling ran. When `impact_agent`'s tool fails and fallback code produces a default score of 0.0, PROV-AGENT records `FINISHED` — the function completed. SPECTRA records `GOAL_FAILED` — the failure was captured before the fallback.

---

## 2. Data Model

| | PROV-AGENT | SPECTRA Causal DAG |
|---|---|---|
| **Standard** | W3C PROV-DM | Custom typed event model T=(E,A,→,λ) |
| **Record types** | AgentTool, AIModelInvocation, prov:used, prov:generated | GOAL_CREATED, REASONING_STEP, TOOL_INVOKED, GOAL_COMPLETED, GOAL_FAILED, GOAL_DELEGATED |
| **Causal link primitive** | `prov:wasInformedBy` (function call hierarchy) | Typed edges via three deterministic reconstruction rules |
| **Cross-agent link** | wasInformedBy follows function calls | Rule 1: GOAL_DELEGATED(A→B) creates explicit cross-agent causal edge |
| **Output per 5-agent run** | 15 records (10 AgentTool + 5 AIModelInvocation) | ~28 events, ~27 causal edges |
| **Failure representation** | None — completed functions are always FINISHED | GOAL_FAILED node with reason and error message |
| **Failure propagation path** | Not reconstructed | Reconstructed: e₅→e₆→e₇ failure chain |

---

## 3. What Each Can and Cannot Answer

| Question | PROV-AGENT | SPECTRA DAG |
|---|---|---|
| What ran, with what inputs/outputs? | ✓ | — |
| Which LLM call informed which decision? | ✓ | — |
| Did the LLM tool-dispatch succeed? | ✗ | ✓ |
| Did fallback code compensate for a failure? | ✗ | ✓ |
| Which agent caused a cross-agent failure? | Partial | ✓ |
| What reasoning step preceded the failure? | ✗ | ✓ |
| Was pipeline success genuine or masked? | ✗ | ✓ |
| Can I reproduce this result? | ✓ | — |
| Full prompt and response text? | Via Flowcept/MongoDB | Not stored |
| Natural language querying? | ✓ (Souza et al. 2025) | Not yet |

---

## 4. The Silent Failure — Concrete Example

`impact_agent` always has a broken tool. Across N=40 runs:

**PROV-AGENT records (Layer 1):**
```
AgentTool: impact_agent   — status: FINISHED
AIModelInvocation: llm_call — latency: 3.1s — status: FINISHED
```
Clean. No anomaly. The function returned normally.

**SPECTRA DAG records (Layer 2):**
```
GOAL_CREATED    — impact_agent
REASONING_STEP  — calling_llm
TOOL_INVOKED    — calculate_deep_impact
GOAL_FAILED     — reason: tool_execution_error, error: {"status":"error"}
GOAL_DELEGATED  — impact_agent → citation_agent (fallback handoff)
```
The tool failure is captured at event 4, before the fallback at event 5.
The contrast — Layer 1 `FINISHED`, Layer 2 `GOAL_FAILED` — is the finding.

---

## 5. Experimental Results

| Metric | PROV-AGENT (L1) | SPECTRA DAG (L2) |
|---|---|---|
| Records per run | 15 (fixed, all 40 runs) | ~28 events, ~27 edges |
| impact_agent outcome | FINISHED — 100% of runs | GOAL_FAILED — 100% of runs |
| Silent failure detected | ✗ | ✓ |
| citation_agent stochastic failure | Invisible (FINISHED) | GOAL_FAILED: 40% tinyllama, 35% Mistral |
| Instrumentation overhead | <0.1% | <0.53% of wall-clock |
| Model-independence | N/A | 100% contrast rate, both models |

---

## 6. Relationship to Souza et al. SC Workshops '25

Souza et al. build a natural language query layer **on top of PROV-AGENT**,
translating queries like *"which task ran longest?"* into MongoDB queries
over PROV-AGENT's Layer 1 records.
Their identified open challenge: *"multi-hop causal graph traversal beyond
what DataFrames can represent."*

SPECTRA's DAGBuilder is precisely that multi-hop causal graph. Where Souza
et al. answer *"what did impact_agent produce?"* (provenance query, Layer 1),
SPECTRA answers *"did impact_agent's tool-dispatch succeed before the fallback
ran?"* (reasoning query, Layer 2). The two systems answer orthogonal questions
over the same MongoDB collection and are designed to coexist.

---

## 7. Summary: The Three-Layer Stack

```
Layer 2 — Reasoning (SPECTRA DAG)
         Did the LLM tool-dispatch succeed?
         Which agent caused the failure?
         Is the pipeline masking failures through fallback code?
              ↑ extends
Layer 1 — Accountability (PROV-AGENT)
         What produced what? Which LLM call was used?
         Can I reproduce this result?
         Natural language querying (Souza et al. 2025)
              ↑ extends
Tier 1  — Operational (OTel / Flowcept)
         Is the system healthy? What did it cost?
         Where is the bottleneck?
```

PROV-AGENT and SPECTRA are not competing systems.
SPECTRA builds on PROV-AGENT's provenance foundation and adds
a second instrumentation layer inside the LLM reasoning loop.
Together they answer questions no single-layer tool can answer alone.
