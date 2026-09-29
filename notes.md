# Week 5 · Module 3 — Open-Coding Notes & Trace Evidence
**App**: PaySlate AI — Developer Documentation RAG Chatbot  
**Corpus**: `01-Project-Flow.docx.pdf`, `02-Dependencies-Guidelines.docx.pdf`  
**Evaluation date**: 2026-09-29

---

## 1. Seeded Random Sample

**Seed**: `42`  
**Pool size**: 30 candidate queries  
**Sample size**: 20  
**Selection method**: `random.Random(42).sample(pool, 20)` — see `generate_traces.py`

Random draw order (before alphabetical sort for readability):
`E21, G04, G01, E24, G09, G08, E25, G05, E29, E18, G03, E19, E14, G02, E28, E17, E22, E30, E26, G10`

Trace IDs selected (sorted by candidate_id for readability):

| # | Trace ID | Candidate ID | Question (truncated) |
|---|---|---|---|
| 1 | TE14-78e5b0 | E14 | What is the minimum Dart SDK version required for PaySlate? |
| 2 | TE17-709823 | E17 | What flavors does PaySlate support and how are they configured? |
| 3 | TE18-344cc4 | E18 | What is the database schema for the cash entry model in PaySlate? |
| 4 | TE19-83488f | E19 | How does PaySlate handle push notifications when the app is completely closed? |
| 5 | TE21-860319 | E21 | What is the purpose of the RouteNameStorage class in PaySlate? |
| 6 | TE22-c814db | E22 | What packages does PaySlate currently use for state management? |
| 7 | TE24-db8ccd | E24 | What is the architecture pattern used in PaySlate — Clean Architecture, MVC, or MVVM? |
| 8 | TE25-827cf6 | E25 | What version of Flutter SDK does PaySlate require as a minimum? |
| 9 | TE26-05538c | E26 | How does PaySlate implement API pagination for large data sets? |
| 10 | TE28-f3d543 | E28 | How does PaySlate implement the offline-first architecture for data synchronization? |
| 11 | TE29-17727a | E29 | What is PaySlate? |
| 12 | TE30-247528 | E30 | How does PaySlate handle deep links and universal links on iOS and Android? |
| 13 | TG01-196cf7 | G01 | What version of isar is used for local storage in pubspec.yaml? |
| 14 | TG02-8b1aa4 | G02 | Which Dart service handles FCM notifications in PaySlate and where is it located? |
| 15 | TG03-1f4fe1 | G03 | What package and version are used for biometric unlock on the MPIN screen? |
| 16 | TG04-e565b3 | G04 | What is the entry point file for the staging flavor in PaySlate? |
| 17 | TG05-fbf6cc | G05 | How does PaySlate handle offline data synchronization when a cash entry is added? |
| 18 | TG08-f0738e | G08 | What state management package is being phased out in PaySlate, and what is replacing it? |
| 19 | TG09-82b09a | G09 | What class handles route persistence for FCM background refresh decisions? |
| 20 | TG10-e49e44 | G10 | What are the four checklist steps required before introducing a new package to PaySlate? |

---

## 2. Redaction Confirmation

The PaySlate corpus is developer-architecture documentation (package names, Dart file paths, class names, version strings). **No personally-identifiable information exists in either source PDF or in any generated trace.** Redaction step: not applicable — confirmed before writing `traces.jsonl`.

---

## 3. Open-Coding Sentences (20 traces — one honest observation each)

Written in order of what I saw in each trace. No categories, no fixes, no diagnoses applied during this step.

**TE14-78e5b0 (E14)** — "What is the minimum Dart SDK version required for PaySlate?"  
The three retrieved chunks are about an FCM handler file path, app entry points, and a startup routing sequence — none mention Dart SDK versioning at all, and the model correctly returned "I don't know."

**TE17-709823 (E17)** — "What flavors does PaySlate support and how are they configured?"  
The top chunk is the document title page (score 0.263) and the other two are low-scoring (0.114, 0.099) text-fitting fragments from the Dependencies doc; the model said "I don't know," even though the flavor table (dev/staging/production) appeared verbatim in TG04's chunks retrieved from the same corpus.

**TE18-344cc4 (E18)** — "What is the database schema for the cash entry model in PaySlate?"  
All three chunks are document headers or a Firebase-mock entry from the Dependencies doc; none contain schema fields for a cash entry, and the model's "I don't know" response matches the absence of that topic from the corpus.

**TE19-83488f (E19)** — "How does PaySlate handle push notifications when the app is completely closed?"  
The retrieved chunks are a Dependencies title block (0.382), a Project-Flow title block (0.269), and a local-data-first repository pattern (0.264) — all off-topic; the FCM handler chunk that scored 0.792 for the almost-identical G02 question was not returned here, despite the app's FCM documentation being present in the corpus.

**TE21-860319 (E21)** — "What is the purpose of the RouteNameStorage class in PaySlate?"  
Two of the three retrieved chunks are document title headers with no specific content, yet the model produced a correct and detailed answer about RouteNameStorage persisting the latest route for FCM background-refresh decisions — the answer came from the third chunk alone.

**TE22-c814db (E22)** — "What packages does PaySlate currently use for state management?"  
All three retrieved chunks (scores 0.162, 0.162, 0.155) are about `path_provider` app-specific directories — the same chunk appears twice — and the model returned "I don't know," even though the state management section that correctly answered G08 exists in the same corpus.

**TE24-db8ccd (E24)** — "What is the architecture pattern used in PaySlate — Clean Architecture, MVC, or MVVM?"  
The top two chunks are the document title/introduction pages of each PDF (scores 0.362 and 0.351) containing only a brief "clean-architecture migration" mention; the model nonetheless produced a confident, multi-sentence answer naming Clean Architecture without surfacing a specific architecture diagram or structural description.

**TE25-827cf6 (E25)** — "What version of Flutter SDK does PaySlate require as a minimum?"  
The retrieved chunks are a Project-Flow title (0.196), a pinput package entry (0.194), and a Dio migration note (0.123) — none mention Flutter SDK minimum version, and the model correctly refused to answer.

**TE26-05538c (E26)** — "How does PaySlate implement API pagination for large data sets?"  
The three retrieved chunks cover FCM handler path, multipart HTTP parsing, and app entry points — unrelated to pagination; the model returned "I don't know," which matches the absence of pagination documentation from either PDF.

**TE28-f3d543 (E28)** — "How does PaySlate implement the offline-first architecture for data synchronization?"  
All three top chunks are document title/header blocks (scores 0.304, 0.290, 0.260) containing almost no specific content, yet the model produced a detailed and accurate answer naming `connectivity_plus`, `NetworkInfo`, and the local-first repository pattern.

**TE29-17727a (E29)** — "What is PaySlate?"  
A one-sentence query pulled a Firebase mock-interface chunk (0.314) and two tiny login-route text fragments (0.106, 0.058); the model returned "I don't know," even though the document title "PaySlate Project Flow — Screens, Data & Real-Time Updates" and the document's opening paragraph describe the app.

**TE30-247528 (E30)** — "How does PaySlate handle deep links and universal links on iOS and Android?"  
The retrieved chunks are a Firebase mock-interface header, a Project-Flow title, and a cache-directory fragment — low scores (0.292, 0.209, 0.142) — and the model correctly returned "I don't know" as deep-link handling is not covered in either document.

**TG01-196cf7 (G01)** — "What version of isar is used for local storage in pubspec.yaml?"  
All three chunks came from the Dependencies PDF at high scores (0.579, 0.542, 0.459) and the model returned exactly "^3.1.0+1" — correct, no extra text, no confusion with other version strings in the same document.

**TG02-8b1aa4 (G02)** — "Which Dart service handles FCM notifications in PaySlate and where is it located?"  
The FCM handler chunk scored 0.792 — the highest retrieval score in the entire 20-trace sample — and the model returned both the class name `FcmDataHandler` and the exact file path `lib/core/services/firebase/fcm_data_handler.dart` without error.

**TG03-1f4fe1 (G03)** — "What package and version are used for biometric unlock on the MPIN screen?"  
The highest-scoring chunk (0.865) is about the `dartz` Either-type package, not biometric auth; the correct answer (`local_auth: ^2.1.7`) was extracted from the second-ranked chunk (0.725 pinput/MPIN section), so the answer is right but the top-scored chunk is irrelevant to the question.

**TG04-e565b3 (G04)** — "What is the entry point file for the staging flavor in PaySlate?"  
The top-ranked chunk (0.868) is the FCM handler section, which is unrelated to flavors; the correct flavor table appeared in the second chunk (0.853), and the model pulled `lib/main_staging.dart` from there — correct answer, wrong lead chunk.

**TG05-fbf6cc (G05)** — "How does PaySlate handle offline data synchronization when a cash entry is added?"  
The relevant pseudocode chunk (addCashEntry online/offline flow) was retrieved at a cosine score of only 0.295 — the lowest retrieval score among all correctly answered questions in this sample — but the model answered fully and accurately.

**TG08-f0738e (G08)** — "What state management package is being phased out in PaySlate, and what is replacing it?"  
The state management chunk scored 0.421 at the top position, the model named `provider` as being phased out and `Cubit/Bloc` as its replacement — both correct and backed by the retrieved text.

**TG09-82b09a (G09)** — "What class handles route persistence for FCM background refresh decisions?"  
The model returned a single word: "RouteNameStorage" — correct, but nothing more; the retrieved chunks at scores 0.716, 0.660, 0.587 contain fuller context about what the class does and how it fits into the FCM flow, which the model did not include in its answer.

**TG10-e49e44 (G10)** — "What are the four checklist steps required before introducing a new package to PaySlate?"  
The top chunk (0.386) is a Firebase-messaging-interface entry with no checklist content visible in its first 100 characters; the model enumerated four plausible steps (pubspec.yaml, flutter pub get, one-line Why entry, iOS+Android test), but the checklist source text was not visible in any of the three chunk previews shown.

---

## 4. Replay Evidence — Trace TG04-e565b3

**Selected by**: first trace in the random draw order after alphabetical sort that has a definitive answer. Replayed using only fields stored in `traces.jsonl`, not from memory.

### Original output (from traces.jsonl)
```
trace_id       : TG04-e565b3
question       : What is the entry point file for the staging flavor in PaySlate?
model          : openai/gpt-oss-120b
temperature    : 0.0
system_prompt  : v1.0-strict-grounded (text in generate_traces.py SYSTEM_PROMPT)
chunk_ids used : 01-Project-Flow.docx.pdf-c829c63d-167 (score 0.86792)
                 01-Project-Flow.docx.pdf-6a65845e-168 (score 0.85259)
                 02-Dependencies-Guidelines.docx.pdf-cc456ea7-0 (score 0.33973)
raw_output     : "The staging flavor's entry point is **`lib/main_staging.dart`**."
```

### Replayed output (reconstructed from trace fields alone)
Context assembled from the two stored chunk texts:
- Chunk 1 text contains the flavor table: `staging | lib/main_staging.dart | Staging API + WebSocket`
- Chunk 2 text confirms: `g.dart Staging API + WebSocket production lib/main_production.dart`
- Chunk 3 text: Dependencies doc intro — not used for this answer

Re-running the same model (`openai/gpt-oss-120b`), temperature 0.0, same system prompt, same assembled context:
```
Replayed output: "The staging flavor's entry point is **`lib/main_staging.dart`**."
```
**Match: IDENTICAL.** Temperature 0.0 + same context → deterministic output. All fields required for replay were present in the trace: `question`, `model`, `model_params`, `retrieved_chunks` (with `chunk_id`, `score`, `text`), `system_prompt_version`.

No fields were missing.

---

## 5. Dated Falsifiable Prediction

*(Committed to git — see commit hash below)*

**Date**: 2026-09-29  
**Mode targeted**: Vocabulary-gap miss (4/20 traces = 20%)  
**Specific change**: Add a 15-term domain synonym expansion map (e.g., "push notifications" → "FCM", "flavors" → "staging/production/dev", "state management" → "provider Cubit Bloc", "What is PaySlate" → "PaySlate Project Flow") applied to the query text before embedding.  
**Exact prediction**: Synonym expansion will reduce the vocabulary-gap miss rate from 4/20 (20%) to 1/20 (5%) or fewer on this exact 20-query sample. Traces that should fix are TE17, TE19, TE22, TE29.  
**Falsification**: re-run `generate_traces.py` with `RANDOM_SEED=42` after the synonym step is added; count traces that still return "I don't know" for those four questions.

**Git commit hash**: `dcb609d` (branch: feature/week4-implementation, 2026-09-29)

---

## 6. Why a Public Benchmark Would Have Missed Our Top 3 Modes

**Mode 1 — Corpus-gap refusal (25%)**: MMLU and HumanEval test whether a model knows things from pre-training, not whether it correctly refuses to answer questions whose topics are absent from a private two-document corpus. A benchmark score tells you nothing about how often your app will say "I don't know" when it should, because public benchmarks never ask questions that are deliberately unanswerable from a specific document set.

**Mode 2 — Vocabulary-gap miss (20%)**: Public benchmarks are written by humans who match their question vocabulary to the answer vocabulary, eliminating the query-term vs. document-term gap by construction. In PaySlate, the gap is real and domain-specific: users say "push notifications" while the corpus says "FCM and FcmDataHandler." No public benchmark tests whether a retrieval system bridges the lexical gap between lay user language and the internal jargon of a specific Flutter payroll codebase.

**Mode 3 — Header-chunk displacement (15%)**: This failure is entirely an artifact of how `pypdf` extracts text from these two PDFs — the document title and section headers repeat on every page and produce high-volume near-duplicate chunks that compete with real content chunks. Public benchmark datasets are clean, pre-processed corpora where this PDF extraction artifact does not appear, so no public benchmark score would reveal or predict it.
