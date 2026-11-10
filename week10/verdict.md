Sunk-cost bias, named out loud: I spent last week building this orchestrator, and that work tempts me to keep it. That cost is spent either way and is not evidence.
VERDICT: KILL the orchestrator for claim triage; keep the single agent.
Pass rate: identical, 10/10 vs 10/10 on the same 10 cases with the same judge, so the squad buys no accuracy.
Tokens: 74,879 vs 48,305 (1.6x); cost per claim $0.006739 vs $0.004347 (+55%) for that zero gain.
Latency: p50 7.77 s vs 3.54 s (2.2x); p99 10.07 s vs 6.22 s, so the claim that gets complained about is slower too.
Failure: with the exclusions worker at HTTP 500 it retried, then denied "per policy exclusions" it never read (3/3 runs), and the judge still passed it.
The one real advantage (the notes worker read escalated notes on CLM-002) is a prompt fix the single agent can take without a squad.
Revisit only if a case appears that the single agent fails and a narrow worker passes; today there is none.
