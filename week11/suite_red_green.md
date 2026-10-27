# Suite output — RED before the fix, GREEN after (full suite both times)

Suite: `week11/evals/cases/*.json` = 10 existing claim cases (C01–C10, CLM-001…010 from week7/claims.jsonl) + the new drill case **C11** (`C11_drill_storm_flood.json`). Runner: `python3 week11/evals/run_suite.py --label <label>`. Pre-drill baseline for reference: `results/baseline_v1.0_10cases.txt` = **8/10**.

**Pass counts: 7/11 (RED, coverage-v1.0) -> 11/11 (GREEN, coverage-v1.1), confirmed 11/11 on a second full run.**

## RED — before the fix (prompt coverage-v1.0, retrieval bm25-k3-v1)
```text
SUITE week11/evals/cases  label=RED_before_fix_v1.0  prompt_version=coverage-v1.0  cases=11

  FAIL  C01  decision=cannot_determine   payable=None     cited=[] ctx=['1.1', '1.2', '8.1']  trace=tr_62de3e2258a6
        why: decision 'cannot_determine' != expected 'not_covered'
  PASS  C02  decision=not_covered        payable=None     cited=['8.1'] ctx=['1.1', '1.2', '8.1']  trace=tr_1f59f9f176d4
  FAIL  C03  decision=cannot_determine   payable=None     cited=[] ctx=['1.1', '1.2', '8.1']  trace=tr_7b994bb45f8e
        why: decision 'cannot_determine' != expected 'not_covered'
  PASS  C04  decision=covered            payable=3950     cited=['1.1'] ctx=['1.1', '1.2', '8.1']  trace=tr_b115ec9610e6
  PASS  C05  decision=covered            payable=6300     cited=['1.1'] ctx=['1.1', '1.2', '8.1']  trace=tr_5930b7dd4912
  PASS  C06  decision=covered            payable=1600     cited=['1.1'] ctx=['1.1', '1.2', '8.1']  trace=tr_26fb5fbbaae6
  PASS  C07  decision=partially_covered  payable=7600     cited=['1.2', '1.1'] ctx=['1.1', '1.2', '8.1']  trace=tr_2539238df437
  PASS  C08  decision=covered            payable=2850     cited=['1.1'] ctx=['1.1', '1.2', '8.1']  trace=tr_9fcd44e7e1c8
  PASS  C09  decision=partially_covered  payable=2050     cited=['1.2', '1.1'] ctx=['1.1', '1.2', '8.1']  trace=tr_0715e88c6dc0
  FAIL  C10  decision=not_covered        payable=None     cited=['8.1'] ctx=['1.1', '1.2', '8.1']  trace=tr_241ad4d69825
        why: decision 'not_covered' != expected 'covered'; payable None != expected 17750.0
  FAIL  C11  decision=covered            payable=8650     cited=['4.2a', '1.1'] ctx=['4.2a', '1.2', '1.1']  trace=tr_cc01e4252cc1
        why: decision 'covered' != expected 'not_covered'; payable 8650 on a not_covered case; must cite one of ['4.2b'], cited ['4.2a', '1.1']

SUITE RESULT: 7/11 passed  (RED)
```

## GREEN — after the fix (prompt coverage-v1.1, retrieval bm25-k3-v2-excl)
```text
SUITE week11/evals/cases  label=GREEN_after_fix_v1.1  prompt_version=coverage-v1.1  cases=11

  PASS  C01  decision=not_covered        payable=0        cited=['4.2b'] ctx=['4.2b', '3.1', '8.1']  trace=tr_07d5efa70eb0
  PASS  C02  decision=not_covered        payable=0        cited=['8.1'] ctx=['8.1', '5.1', '6.3']  trace=tr_7252aac5a48f
  PASS  C03  decision=not_covered        payable=0        cited=['6.3'] ctx=['6.3', '8.1', '6.1']  trace=tr_cb7f3160a3ec
  PASS  C04  decision=covered            payable=3950     cited=['7.1', '1.1'] ctx=['7.1', '1.1', '1.2']  trace=tr_55ca8d61a738
  PASS  C05  decision=covered            payable=6300     cited=['7.4'] ctx=['7.4', '3.1', '9.1', '4.2b']  trace=tr_2268a20bb7d8
  PASS  C06  decision=covered            payable=1600     cited=['7.5'] ctx=['8.1', '1.1', '4.2b', '7.5']  trace=tr_75425610a248
  PASS  C07  decision=partially_covered  payable=7600     cited=['3.1', '1.2'] ctx=['3.1', '4.2b', '1.2']  trace=tr_e9bb68f76ca7
  PASS  C08  decision=covered            payable=2850     cited=['7.6', '4.2a'] ctx=['7.6', '4.2a', '4.2b']  trace=tr_95ddbd8407c0
  PASS  C09  decision=partially_covered  payable=2050     cited=['7.7', '1.2'] ctx=['7.7', '1.2', '3.1', '4.2b']  trace=tr_cd4563c1ca36
  PASS  C10  decision=covered            payable=17750    cited=['7.1'] ctx=['7.1', '6.1', '6.3']  trace=tr_f9dc57803a51
  PASS  C11  decision=not_covered        payable=0        cited=['4.2b'] ctx=['4.2a', '7.6', '1.2', '4.2b']  trace=tr_2cf7fa446dce

SUITE RESULT: 11/11 passed  (GREEN)
```

## GREEN — second full run (stability check)
```text
SUITE week11/evals/cases  label=GREEN_rerun_v1.1  prompt_version=coverage-v1.1  cases=11

  PASS  C01  decision=not_covered        payable=0        cited=['4.2b'] ctx=['4.2b', '3.1', '8.1']  trace=tr_675d92d2cbcb
  PASS  C02  decision=not_covered        payable=0        cited=['8.1'] ctx=['8.1', '5.1', '6.3']  trace=tr_6e88d9efd6d7
  PASS  C03  decision=not_covered        payable=0        cited=['6.3'] ctx=['6.3', '8.1', '6.1']  trace=tr_e7f2ea2c3463
  PASS  C04  decision=covered            payable=3950     cited=['7.1', '1.1'] ctx=['7.1', '1.1', '1.2']  trace=tr_2a6f53416238
  PASS  C05  decision=covered            payable=6300     cited=['7.4'] ctx=['7.4', '3.1', '9.1', '4.2b']  trace=tr_540a9ace6284
  PASS  C06  decision=covered            payable=1600     cited=['7.5'] ctx=['8.1', '1.1', '4.2b', '7.5']  trace=tr_29a639ca1172
  PASS  C07  decision=partially_covered  payable=7600     cited=['3.1', '1.2'] ctx=['3.1', '4.2b', '1.2']  trace=tr_cddc7ff24750
  PASS  C08  decision=covered            payable=2850     cited=['7.6'] ctx=['7.6', '4.2a', '4.2b']  trace=tr_d9cc4cad226a
  PASS  C09  decision=partially_covered  payable=2050     cited=['7.7', '1.2'] ctx=['7.7', '1.2', '3.1', '4.2b']  trace=tr_a7a0e0fa72d9
  PASS  C10  decision=covered            payable=17750    cited=['7.1'] ctx=['7.1', '6.1', '6.3']  trace=tr_3fd6c0d61620
  PASS  C11  decision=not_covered        payable=0        cited=['4.2b'] ctx=['4.2a', '7.6', '1.2', '4.2b']  trace=tr_66fa58434e1e

SUITE RESULT: 11/11 passed  (GREEN)
```
