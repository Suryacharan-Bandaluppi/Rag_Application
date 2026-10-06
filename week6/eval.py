"""
Week 6 — Single-command eval runner.

Usage:
    python3 week6/eval.py

Runs:
  1. Four deterministic assertions on all 25 cases.
  2. LLM judge v1 on all 25 cases.
  3. LLM judge v2 on all 25 cases (requires judge_v2.txt to exist).
  4. Computes agreement_before (v1 vs hand labels) and
     agreement_after (v2 vs hand labels).
  5. Prints pass-rate-by-mode table for both judge versions.
  6. Writes results to week6/results.json.
"""

import json, re, sys, time, pathlib
from collections import defaultdict

ROOT     = pathlib.Path(__file__).resolve().parent.parent
WEEK6    = pathlib.Path(__file__).resolve().parent
CASES_F  = WEEK6 / "eval_cases.jsonl"
LABELS_F = WEEK6 / "labels_25.json"
JUDV1_F  = WEEK6 / "judge_v1.txt"
JUDV2_F  = WEEK6 / "judge_v2.txt"
RESULTS_F= WEEK6 / "results.json"

sys.path.insert(0, str(ROOT))
from app.rag_engine import call_groq_api
from app.config     import get_groq_api_key, get_groq_model

TRACE_ID_RE = re.compile(r'^T[A-Z]{1,2}\d{2}-[a-f0-9]{6}$')


# ─── deterministic assertions ─────────────────────────────────────────────────

def assert_trace_id_format(case: dict) -> tuple[bool, str]:
    ok = bool(TRACE_ID_RE.match(case.get("trace_id", "")))
    return ok, "" if ok else f"trace_id '{case.get('trace_id')}' does not match T[A-Z]NN-xxxxxx"

def assert_timestamp_parseable(case: dict) -> tuple[bool, str]:
    from datetime import datetime
    ts = case.get("timestamp", "")
    try:
        datetime.fromisoformat(ts)
        return True, ""
    except Exception:
        return False, f"timestamp '{ts}' is not ISO-parseable"

def assert_scores_numeric(case: dict) -> tuple[bool, str]:
    for c in case.get("retrieved_chunks", []):
        s = c.get("score")
        if not isinstance(s, (int, float)) or not (0.0 <= float(s) <= 10.0):
            return False, f"chunk {c.get('chunk_id')} score '{s}' is not a valid float in [0,10]"
    return True, ""

def assert_chunks_logged_on_refusal(case: dict) -> tuple[bool, str]:
    answer = (case.get("raw_output") or "").lower()
    is_refusal = "i don't know" in answer or "i do not know" in answer
    chunks = case.get("retrieved_chunks", [])
    if is_refusal and len(chunks) == 0:
        return False, "Answer is a refusal but no retrieved chunks were logged"
    return True, ""

ASSERTIONS = [
    ("assert_trace_id_format",        assert_trace_id_format),
    ("assert_timestamp_parseable",    assert_timestamp_parseable),
    ("assert_scores_numeric",         assert_scores_numeric),
    ("assert_chunks_on_refusal",      assert_chunks_logged_on_refusal),
]


# ─── LLM judge ────────────────────────────────────────────────────────────────

def format_chunks_for_judge(chunks: list) -> str:
    parts = []
    for i, c in enumerate(chunks, 1):
        parts.append(
            f"[Chunk {i} | ID: {c.get('chunk_id','?')} | "
            f"Source: {c.get('source','?')} | Score: {c.get('score',0):.4f}]\n"
            f"{c.get('text','').strip()}"
        )
    return "\n\n---\n\n".join(parts) or "No chunks retrieved."

def run_judge(case: dict, prompt_template: str, api_key: str, model: str) -> tuple[str, str]:
    """Returns (verdict, reason) where verdict is PASS or FAIL."""
    chunks_text = format_chunks_for_judge(case.get("retrieved_chunks", []))
    prompt = prompt_template.replace("{query}",  case.get("question", ""))
    prompt = prompt.replace("{chunks}", chunks_text)
    prompt = prompt.replace("{answer}", case.get("raw_output", ""))

    for attempt in range(3):
        try:
            raw = call_groq_api(
                api_key=api_key,
                model=model,
                system_prompt="You are a strict evaluator. Follow the instructions exactly.",
                user_prompt=prompt,
            )
            # Parse first line: PASS: reason OR FAIL: reason
            first_line = raw.strip().split("\n")[0].strip()
            if first_line.upper().startswith("PASS"):
                return "PASS", first_line[5:].lstrip(": ").strip()
            elif first_line.upper().startswith("FAIL"):
                return "FAIL", first_line[5:].lstrip(": ").strip()
            # fallback: look for word in full response
            if "PASS" in raw.upper():
                return "PASS", raw[:120]
            return "FAIL", raw[:120]
        except Exception as e:
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
            else:
                return "ERROR", str(e)

    return "ERROR", "max retries exceeded"


# ─── reporting ────────────────────────────────────────────────────────────────

def print_assertion_table(results: list):
    print("\n┌─ DETERMINISTIC ASSERTIONS (4 total, moved out of judge) " + "─" * 20)
    totals = defaultdict(lambda: {"pass": 0, "fail": 0, "msgs": []})
    for r in results:
        for name, ok, msg in r["assertions"]:
            if ok:
                totals[name]["pass"] += 1
            else:
                totals[name]["fail"] += 1
                totals[name]["msgs"].append(msg)

    for name, t in totals.items():
        n = t["pass"] + t["fail"]
        status = "✓" if t["fail"] == 0 else "✗"
        print(f"  {status} {name:<33} {t['pass']}/{n} PASS", end="")
        if t["fail"]:
            print(f"  ← {t['fail']} FAIL: {t['msgs'][0][:60]}", end="")
        print()

    lj_count = 2  # judged criteria count
    da_count = len(ASSERTIONS)
    print(f"\n  Deterministic assertions: {da_count}  |  LLM-judged criteria: {lj_count}")
    print("└" + "─" * 77)


def print_judge_table(results: list, judge_version: str, labels: dict):
    verdicts = {r["trace_id"]: r[f"judge_{judge_version}"]["verdict"] for r in results}
    mode_stats = defaultdict(lambda: {"pass": 0, "fail": 0, "error": 0})

    for r in results:
        v   = verdicts[r["trace_id"]]
        mode = r["mode"]
        if v == "PASS":
            mode_stats[mode]["pass"] += 1
        elif v == "FAIL":
            mode_stats[mode]["fail"] += 1
        else:
            mode_stats[mode]["error"] += 1

    total_pass = sum(s["pass"] for s in mode_stats.values())
    total      = len(results)

    print(f"\n┌─ LLM JUDGE {judge_version.upper()} — pass rate by mode " + "─" * 30)
    print(f"  {'Mode':<22} {'Cases':>5} {'PASS':>5} {'FAIL':>5} {'Pass%':>6}")
    print("  " + "─" * 47)
    for mode in ["corpus_gap","vocabulary_gap","header_displacement","rank_mismatch","low_floor","ok"]:
        s = mode_stats.get(mode, {"pass":0,"fail":0,"error":0})
        n = s["pass"] + s["fail"] + s["error"]
        if n == 0:
            continue
        pct = 100 * s["pass"] / n
        print(f"  {mode:<22} {n:>5} {s['pass']:>5} {s['fail']:>5} {pct:>5.1f}%")
    print("  " + "─" * 47)
    print(f"  {'TOTAL':<22} {total:>5} {total_pass:>5} {total-total_pass:>5} "
          f"{100*total_pass/total:>5.1f}%")

    # Agreement with hand labels
    agree = sum(
        1 for r in results
        if verdicts[r["trace_id"]] == labels.get(r["trace_id"], {}).get("label","?")
    )
    print(f"\n  agreement_{judge_version}: {agree}/{total} = {100*agree/total:.1f}%")
    print("└" + "─" * 77)
    return agree, total


def print_disagreements(results: list, labels: dict, judge_version: str):
    print(f"\n── Disagreements (judge_{judge_version} vs hand labels) " + "─" * 28)
    count = 0
    for r in results:
        tid     = r["trace_id"]
        j_v     = r[f"judge_{judge_version}"]["verdict"]
        h_label = labels.get(tid, {}).get("label", "?")
        if j_v != h_label:
            count += 1
            h_reason = labels.get(tid, {}).get("reasoning", "")
            j_reason = r[f"judge_{judge_version}"]["reason"]
            print(f"\n  [{count}] {tid} | mode={r['mode']}")
            print(f"  Q: {r['question'][:80]}")
            print(f"  Human={h_label}: {h_reason[:100]}")
            print(f"  Judge={j_v}: {j_reason[:100]}")
    if count == 0:
        print("  (no disagreements — perfect agreement)")
    return count


# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    api_key = get_groq_api_key()
    model   = get_groq_model()

    # Load cases
    cases = [json.loads(l) for l in CASES_F.read_text().splitlines() if l.strip()]

    # Load hand labels
    labels_data = json.loads(LABELS_F.read_text())
    labels = {lb["trace_id"]: lb for lb in labels_data["labels"]}

    # Load judge prompts
    jv1_template = JUDV1_F.read_text()
    jv2_template = JUDV2_F.read_text() if JUDV2_F.exists() else None

    print("=" * 80)
    print("  PaySlate RAG — Week 6 Eval  |  25 cases  |  seed=42 + 2 regression replays")
    print("=" * 80)

    # ── 1. Deterministic assertions ───────────────────────────────────────────
    results = []
    for case in cases:
        assr = []
        for name, fn in ASSERTIONS:
            ok, msg = fn(case)
            assr.append((name, ok, msg))
        results.append({
            "trace_id":  case["trace_id"],
            "candidate_id": case["candidate_id"],
            "mode":      case["mode"],
            "question":  case["question"],
            "answer":    case.get("raw_output",""),
            "assertions": assr,
        })

    print_assertion_table(results)

    # ── 2. Judge v1 ───────────────────────────────────────────────────────────
    print(f"\nRunning judge v1 on 25 cases via {model} …")
    for i, (case, r) in enumerate(zip(cases, results), 1):
        verdict, reason = run_judge(case, jv1_template, api_key, model)
        r["judge_v1"] = {"verdict": verdict, "reason": reason}
        sys.stdout.write(f"\r  {i:02d}/25  {case['trace_id']}  → {verdict}    ")
        sys.stdout.flush()
        time.sleep(0.3)
    print()

    agree_v1, total = print_judge_table(results, "v1", labels)
    disag_count = print_disagreements(results, labels, "v1")

    # ── 3. Judge v2 ───────────────────────────────────────────────────────────
    agree_v2 = None
    if jv2_template:
        print(f"\nRunning judge v2 on 25 cases via {model} …")
        for i, (case, r) in enumerate(zip(cases, results), 1):
            verdict, reason = run_judge(case, jv2_template, api_key, model)
            r["judge_v2"] = {"verdict": verdict, "reason": reason}
            sys.stdout.write(f"\r  {i:02d}/25  {case['trace_id']}  → {verdict}    ")
            sys.stdout.flush()
            time.sleep(0.3)
        print()
        agree_v2, _ = print_judge_table(results, "v2", labels)
        print_disagreements(results, labels, "v2")
    else:
        print("\n  [judge_v2.txt not found — skipping v2. Write it, then re-run eval.py]")

    # ── 4. Summary ────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("  SUMMARY")
    print(f"  Hand labels: 17 PASS / 8 FAIL  (8 vocabulary-gap misses labeled FAIL)")
    print(f"  Deterministic assertions: 4  |  LLM-judged criteria: 2")
    print(f"  agreement_before (judge_v1): {agree_v1}/{total} = {100*agree_v1/total:.1f}%")
    if agree_v2 is not None:
        print(f"  agreement_after  (judge_v2): {agree_v2}/{total} = {100*agree_v2/total:.1f}%")
        delta = agree_v2 - agree_v1
        print(f"  delta: {'+' if delta>=0 else ''}{delta} cases ({100*delta/total:+.1f}%)")
    print("=" * 80)

    # Save results
    RESULTS_F.write_text(json.dumps(
        {"cases": results, "agreement_v1": agree_v1, "agreement_v2": agree_v2, "total": total},
        indent=2, ensure_ascii=False
    ))
    print(f"\n  Results saved to {RESULTS_F}")


if __name__ == "__main__":
    main()
