# cost_by_stage.md — cost per query by stage

Request **tr_044698c89a65** (2026-10-26T14:41:17Z, excess_question, prompt `coverage-v1.0`, retrieval `bm25-k3-v1`, model `openai/gpt-oss-120b`). Pricing: $0.90 per 1M tokens in and out (same constant as weeks 7–10). Retrieval is a local BM25 index, so it uses no model tokens and costs $0 in API spend.

| Stage | Spans | Tokens | Latency | Cost (USD) | Share of cost |
|---|---|---|---|---|---|
| retrieval | retrieve.policy_clauses | 0 | 0 ms | $0.0000000 | 0.0% |
| tools | — | 0 | 0 ms | $0.0000000 | 0.0% |
| generation | llm.generate | 793 | 1339 ms | $0.0007137 | 100.0% |
| **total** | | **793** | **1339 ms** | **$0.0007137** | 100% |

For comparison, mean per query over all 46 production requests on 2026-10-26:

| Stage | Mean cost (USD) | Share |
|---|---|---|
| retrieval | $0.0000000 | 0.0% |
| tools | $0.0002168 | 19.5% |
| generation | $0.0008939 | 80.5% |
| **total** | **$0.0011107** | 100% |

## Reading it

- **retrieval $0**: BM25 runs locally, so it has latency (<1 ms) but no API spend. Its cost lives in generation: every retrieved clause becomes prompt tokens.
- **tools $0 on this request**: it is a general question with no claim number, so the model never called `get_claim`. Across the day, tools are 19.5% of mean cost (the LLM turn that requests `get_claim` on claim lookups).
- **generation is 100% of this request's spend** (547 prompt + 246 completion tokens).

## Same question after the fix (eval trace tr_2cf7fa446dce, prompt `coverage-v1.1`, retrieval `bm25-k3-v2-excl`)

| Stage | Tokens | Latency | Cost (USD) |
|---|---|---|---|
| retrieval | 0 | 1 ms | $0.0000000 |
| tools | 0 | 0 ms | $0.0000000 |
| generation | 1,122 | 1554 ms | $0.0010098 |
| **total** | **1,122** | **1555 ms** | **$0.0010098** |

Fix price across the whole 11-case suite: mean **1,630 → 1,837 tokens/query (+12.7%)**, **$0.0014674 → $0.0016534 (+12.7%)**. The extra cost is the exclusion clauses added to context. p50 latency 2.48 s → 2.27 s (second v1.1 run: 3.14 s), within run-to-run noise.
