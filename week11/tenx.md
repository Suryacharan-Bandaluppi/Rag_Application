**At 10× today's claim volume a rate limit breaks first, the daily token cap: 10 × 56,769 tokens/day = 567,690 against Groq's 200,000 TPD for gpt-oss-120b, so it is hit at 3.5× (3.1× once the fix's +12.7% tokens ship). Long before then, cost ($0.51/day) and latency (p50 1.5 s) have not moved.**

Evidence (`evidence/capacity.json`, from `python3 week11/capacity.py` over the 46 production traces of 2026-10-26):

| Candidate | Today | At 10× | Limit | Breaks at |
|---|---|---|---|---|
| **Tokens per day** | 56,769 | **567,690** | **200,000 TPD** | **3.5×** (v1.1: 3.1×) |
| Requests per day (LLM calls) | 68 | 680 | 1,000 RPD | 14.7× |
| Tokens per minute (busiest-hour average) | 155 | 1,547 | 8,000 TPM | 51.7× |
| Cost per day | $0.051 | $0.51 | none | — |
| Latency p50 / p99 | 1.5 s / 13.3 s | unchanged until a limit returns 429 | — | — |

- **Where the limits come from.** RPD 1,000 and TPM 8,000 are read live from Groq's response headers. TPD 200,000 comes from Groq's published rate-limit table (console.groq.com/docs/rate-limits) for `openai/gpt-oss-120b`, in the same row whose RPD and TPM match our headers. The headers do not report TPD, so it is the one limit not measured directly.
- **Why not the single peak minute.** Multiplying the single busiest minute by 10 (3,376 → 33,760 TPM) would claim a break at 2.4×. That treats one heavy 3.4k-token request as if it arrived 10 times in the same minute, so it overstates the burst. The busiest-hour average is the defensible TPM figure.
- **The quota is shared.** Eval runs and development calls use the same key, so the real headroom is smaller than this table shows.
