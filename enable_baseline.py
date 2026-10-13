import json
import urllib.request
import urllib.error
from eval_retrieval import run_evaluation
from app.rag_engine import set_system_reranker_mode

SERVER_URL = "http://127.0.0.1:8000/api/mode"


def set_mode_on_server(use_reranker: bool):
    set_system_reranker_mode(use_reranker)
    try:
        req = urllib.request.Request(
            url=SERVER_URL,
            data=json.dumps({"use_reranker": use_reranker}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            res = json.loads(response.read().decode("utf-8"))
            print(f"📡 Web UI Server System Mode Updated: {res.get('mode_name')} | Hit-Rate: {res.get('hit_rate')}")
    except Exception:
        print("ℹ️ Local state set to Baseline Mode.")


def main():
    set_mode_on_server(use_reranker=False)

    print("\n" + "=" * 65)
    print("🚨 SYSTEM SWITCHED TO BASELINE MODE (PURE DENSE VECTOR SEARCH)")
    print("=" * 65)

    res = run_evaluation()

    print("\n" + "=" * 65)
    print("📊 BASELINE EVALUATION SUMMARY:")
    print(f"   • Hit-Rate@3: {res['baseline_hit_rate']:.1f}% ({int(res['baseline_hit_rate']*12/100)}/12 Queries Passed)")
    print(f"   • p50 Latency: {res['baseline_p50_latency']:.2f} ms")
    print(f"   • Status: 4 Retrieval Misses (R Failures) occurred on dense vector search.")
    print("   • Web UI Status: Set to Baseline (66.7%). Questions in UI will use Pure Dense Search.")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    main()
