# AI Codebase Engineer — Roadmap

Status legend: ⬜ Not started · 🟨 In progress · ✅ Done

Update this file as we finish each part. Keep notes under each item — decisions,
gotchas, what we deferred — so future-us doesn't relitigate them.

---

## Phase 0 — Foundations
⬜ Decide tech stack: Python (FastAPI) backend, React/TS frontend, Postgres +
   a vector store (start with Chroma/pgvector, no need for Pinecone yet)
⬜ Repo scaffolding created (this structure)
⬜ Docker Compose for local dev (backend, frontend, db, vector store)
⬜ Basic CI (lint + tests on push)

Notes:
-

---

## Phase 1 — Codebase Ingestion
✅ Accept a local folder upload (zip) → extract into `data/repos/<project_id>`
✅ Accept a Git URL → clone via `gitpython`
✅ File walker: skips binaries/node_modules/venv/build dirs/oversized files
✅ Language detection per file (extension-based)
⬜ Persist project metadata (id, source, languages found, file count) in DB —
   deferred: currently stateless, re-walks disk on every summary request.
   Fine for now, revisit once we have Postgres wired up for real.

Notes:
- Tested live against a real GitHub repo (plant-disease-detector) via
  `/api/projects/from-git` — returned correct file count + language
  breakdown through the Swagger UI (/docs). Working end to end.
- `.gitignore`-respecting walk not implemented yet — we use a hardcoded
  SKIP_DIRS set instead, which is good enough for now but doesn't honor a
  project's own .gitignore rules. Note for later polish.

---

## Phase 2 — Parsing (AST layer)
⬜ Integrate `tree-sitter` with grammars for Python, JS/TS, Java, C/C++
   (tree-sitter is the right call here — one consistent API across languages,
   incremental parsing, well-maintained)
⬜ Build a language-agnostic "symbol" representation: functions, classes,
   imports, calls — normalized across languages so downstream code doesn't
   care which language it came from
⬜ Extract per-file: definitions, imports, function signatures, docstrings
⬜ Build a dependency graph: file → file (imports), function → function (calls)

Notes:
-

---

## Phase 3 — Indexing (retrieval layer)
⬜ Chunking strategy — chunk by function/class, not fixed token windows
   (this matters a lot for code; naive chunking breaks context badly)
⬜ Generate embeddings for each chunk (start with a hosted embedding model)
⬜ Store in vector DB, keyed to project_id + file path + line range
⬜ Store the dependency graph separately (Postgres or even just JSON/NetworkX
   graph) — retrieval will combine semantic search + graph traversal
⬜ Hybrid retrieval: semantic search for "what's relevant" + graph traversal
   for "what depends on this"

Notes:
-

---

## Phase 4 — Codebase Chat (RAG Q&A)
⬜ Basic RAG: question → retrieve relevant chunks → answer with citations
   (file:line references, not vague prose)
⬜ Multi-hop questions: "which files depend on UserService" needs graph
   traversal, not just semantic search — route queries appropriately
⬜ Conversation memory within a session (follow-up questions about same code)
⬜ Test against a real medium-sized open source repo, not a toy example

Notes:
-

---

## Phase 5 — Bug / Issue Detection
⬜ Start with pattern-based static checks (SQL injection via string
   formatting, hardcoded secrets, obvious null-deref patterns) — cheap,
   reliable, no LLM needed for these
⬜ Layer LLM-based review on top for things static analysis misses (logic
   bugs, bad error handling, race conditions) — feed it function + its
   callers/callees from the graph for context
⬜ Output format: file, line range, severity, explanation, suggested fix
⬜ De-dupe and rank findings so it's not just a wall of noise

Notes:
-

---

## Phase 6 — Test Generation
⬜ Given a function + its signature + surrounding context, generate test
   cases: normal, edge, invalid input, boundary
⬜ Language-specific test scaffolding (pytest, jest, junit)
⬜ Run the generated tests against the actual code to check they at least
   execute without erroring (before showing them to the user)
⬜ Coverage-aware: prioritize untested functions using existing coverage data
   if available

Notes:
-

---

## Phase 7 — Fix Generation & Patching
⬜ Given a bug finding, generate a proposed diff (not full file rewrite —
   minimal patch)
⬜ Diff format: unified diff, applyable with `git apply` or similar
⬜ Explain *why* the fix works, not just what changed

Notes:
-

---

## Phase 8 — Verification Loop (the hard, interesting part)
⬜ Sandboxed execution environment (Docker container per project) to run
   tests safely — never run untrusted/generated code on the host
⬜ Apply patch → run test suite → capture pass/fail + output
⬜ On failure: feed test output back to the agent, let it retry (cap retries,
   e.g. 3 attempts, to avoid infinite loops / runaway cost)
⬜ On success: present diff + explanation + test results to user for approval
   before merging — **never auto-commit without human approval**, at least
   at this stage
⬜ Rollback mechanism if something goes wrong

Notes:
-

---

## Phase 9 — Agent Orchestration
⬜ Define the tool interface (search codebase, read file, propose patch,
   run tests, ask clarifying question) — agent picks tools, doesn't contain
   subsystem logic itself
⬜ Planning loop: given a user goal ("fix the bug in auth.py"), break into
   steps, call tools, observe results, adjust
⬜ Guardrails: max steps, max cost per session, confirmation before any
   file-modifying action

Notes:
-

---

## Phase 10 — Frontend
⬜ Project upload / connect repo flow
⬜ Codebase map visualization (tree + dependency graph view)
⬜ Findings dashboard (bugs, suggested tests, suggested fixes)
⬜ Diff viewer with approve/reject
⬜ Chat interface for codebase Q&A

Notes:
-

---

## Phase 11 — Polish / Productionization (later)
⬜ Auth & multi-user support
⬜ Background job queue for long ingestion/analysis (Celery or similar)
⬜ Cost/usage tracking per project
⬜ Support for larger repos (incremental re-indexing on file change, not
   full re-index every time)

---

## Open Decisions
- LLM provider: which model(s) for agent reasoning vs cheaper models for
  bulk tasks like embedding/simple pattern checks?
- Vector store: Chroma (simple, local) vs pgvector (one less service) vs
  something hosted?
- How far do we actually take "auto-apply fixes"? Recommend starting
  human-in-the-loop only, revisit after Phase 8 works reliably.

## Decision Log
(Add dated entries here as we make real choices, so we remember why.)