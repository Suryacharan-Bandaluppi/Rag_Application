# Week 5 · Module 3 — Failure Mode Taxonomy
**App**: PaySlate AI — Developer Documentation RAG Chatbot  
**Sample**: 20 traces, seed=42, generated 2026-09-29  
**Corpus**: `01-Project-Flow.docx.pdf`, `02-Dependencies-Guidelines.docx.pdf` (401 chunks total)

---

## Ranked Failure Mode Table

| Rank | Mode Name | Count | Freq % | Severity | Example Trace ID |
|------|-----------|-------|--------|----------|-----------------|
| 1 | **Corpus-gap refusal** — model correctly says "I don't know" because the topic does not appear in either document | 5 | 25% | Low — expected behavior; user gets an honest refusal, not a wrong answer | TE14-78e5b0 |
| 2 | **Vocabulary-gap miss** — model wrongly says "I don't know" because the query uses different words than the document, even though the answer exists in the corpus | 4 | 20% | High — user loses a valid answer; the system appears broken when it is not | TE19-83488f |
| 3 | **Header-chunk displacement** — document title/header chunks occupy top-2 retrieval slots, pushing actual content chunks to position 3 or out of the window entirely | 3 | 15% | Medium — answer sometimes survives if content lands in slot 3; silent failure if it does not | TE21-860319 |
| 4 | **Lead-chunk score-answer mismatch** — the highest-scored retrieved chunk does not contain the answer; the LLM extracts from a lower-ranked chunk, making the cited source misleading | 2 | 10% | Medium — answer is correct but the attribution is wrong; a cited source check would flag this | TG03-1f4fe1 |
| 5 | **Low-floor correct retrieval** — the target chunk is retrieved but at cosine similarity < 0.30, meaning a slightly stricter cutoff or a harder query would drop it | 2 | 10% | Medium — system works today but is brittle; the margin between hit and miss is thin | TG05-fbf6cc |

**OK (no failure)**: 4 traces (20%) — TG01, TG02, TG08, TG09

---

## Mode Detail Cards

### Mode 1 · Corpus-gap refusal — 5 / 20 (25%)
**What happened**: The query asks about a topic — Dart SDK minimum version, cash-entry DB schema, Flutter SDK version, API pagination, deep-link handling — that is simply not present in either PDF. The retrieval engine returns off-topic chunks at low similarity scores (all ≤ 0.38), and the model correctly refuses.

**Severity**: Low. The model is doing its job. The documentation gap is the real issue, not the retrieval or generation.

**Traces**: TE14, TE18, TE25, TE26, TE30

**Example** (TE14-78e5b0): Query asks for Dart SDK minimum version. Top chunks are about the FCM handler file path (score 0.251), app entry points (0.213), and a startup routing snippet (0.119) — none mention SDK requirements.

---

### Mode 2 · Vocabulary-gap miss — 4 / 20 (20%) ← FIX TARGET
**What happened**: The user's phrasing and the document's vocabulary diverge. The corpus answers the question (the FCM section answers "push notifications", the flavor table answers "flavors", the state-management section answers "state management packages", the title answers "What is PaySlate"), but the embedding space does not bridge the gap. Retrieval returns title blocks or unrelated package entries instead.

**Severity**: High. The user gets a false "I don't know" — the system looks broken when the knowledge is there. Four in twenty is 20% of queries silently failing.

**Traces**: TE17 (flavors ↔ staging/dev/production), TE19 (push notifications ↔ FCM), TE22 (state management ↔ provider/Cubit), TE29 (What is PaySlate ↔ document opening paragraph)

**Example** (TE19-83488f): "How does PaySlate handle push notifications when the app is completely closed?" retrieved only title blocks and a local-data-first snippet. The FCM handler chunk that scored 0.792 for the synonym question "Which Dart service handles FCM notifications?" (TG02) was not returned at all.

**Planned fix**: 15-term domain synonym expansion applied to the query before embedding (see dated prediction in notes.md).

---

### Mode 3 · Header-chunk displacement — 3 / 20 (15%)
**What happened**: Both PDFs repeat their document title and section header on every page when extracted by `pypdf`. This produces many near-duplicate header chunks that accumulate high cosine similarity with broad questions, occupying retrieval slots 1 and 2 and leaving only one slot for real content.

**Severity**: Medium. In two of three cases (TE21, TE28) the LLM still produced a correct answer from the remaining content chunk. In one case (TE24) the confident-sounding answer rested on a single passing title-level reference rather than a structural description — fragile under follow-up.

**Traces**: TE21 (2/3 chunks are title headers; answer extracted from chunk 3), TE24 (top 2 chunks are doc titles; "Clean Architecture" answer from one brief title mention), TE28 (all 3 chunks are header blocks; detailed offline-first answer still generated — source unclear).

**Example** (TE21-860319): "What is the purpose of the RouteNameStorage class?" — chunk 1 (score 0.384) is the Dependencies doc title; chunk 2 (0.346) is the Project-Flow title. The correct answer came from chunk 3 alone.

---

### Mode 4 · Lead-chunk score-answer mismatch — 2 / 20 (10%)
**What happened**: The reranker placed a chunk first based on broad token overlap, but that chunk's content is unrelated to the specific question. The correct answer came from the second-ranked chunk, meaning the model's sourced citation points to the wrong section.

**Severity**: Medium. Answers are factually correct, but any system that surfaces the top chunk as the citation would mislead the user about where the information came from.

**Traces**: TG03 (dartz/Either-type chunk ranked #1 at 0.865; local_auth answer from pinput/MPIN chunk ranked #2 at 0.725), TG04 (FCM handler chunk ranked #1 at 0.868; staging entry point from flavor-table chunk ranked #2 at 0.853).

**Example** (TG03-1f4fe1): "What package is used for biometric unlock on the MPIN screen?" — the top chunk is about `dartz: ^0.10.1` (a functional programming helper), not biometrics. The correct `local_auth: ^2.1.7` answer lives in the second chunk.

---

### Mode 5 · Low-floor correct retrieval — 2 / 20 (10%)
**What happened**: The target chunk was retrieved but at a cosine similarity well below the cluster of high-confidence hits. A stricter score threshold or a harder synonym-variant of the same question would likely push this chunk out of the top-3 window.

**Severity**: Medium. The system answers correctly now, but the margin is thin and reliability is not guaranteed for paraphrase variants.

**Traces**: TG05 (offline sync pseudocode at score 0.295 — lowest hit score in sample), TG10 (package checklist section not visible in any chunk's first 100 characters; model may have accessed the content from further into the chunk body).

**Example** (TG05-fbf6cc): "How does PaySlate handle offline data synchronization when a cash entry is added?" — the pseudocode chunk that contains the answer scored only 0.295; the second and third chunks are a Dependencies title block and an FCM handler reference.

---

## Fix Priority Rationale

| Priority | Mode | Reason |
|----------|------|--------|
| **1 — Attack next** | Vocabulary-gap miss | Highest severity, 4/20 frequency, falsifiable fix available (synonym expansion), fix does not change retrieval infrastructure |
| **2 — Soon** | Header-chunk displacement | Affects 3/20, fragile even when answers survive; fix is chunking strategy (de-duplicate header chunks at ingest time) |
| **3 — Later** | Lead-chunk score-answer mismatch | Affects citation correctness, not answer correctness; fix is reranker calibration |
| **4 — Monitor** | Low-floor correct retrieval | Affects robustness, not current accuracy; fix is query augmentation after Mode 2 is addressed |
| **5 — Accept** | Corpus-gap refusal | Correct behavior; fix is documentation coverage, not the RAG system |
