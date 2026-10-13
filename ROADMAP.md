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
✅ Integrate `tree-sitter` for Python (JS/TS/Java/C++ deferred — see notes)
⬜ Build a language-agnostic "symbol" representation normalized across
   languages — currently Python-specific dataclasses; revisit once we add
   a second language, so we don't guess the right abstraction prematurely
✅ Extract per-file: function/class definitions, imports, parameters,
   docstrings, line ranges
⬜ Build a dependency graph: file → file (imports), function → function
   (calls) — this is Phase 3 territory now; imports are extracted but not
   yet resolved into an actual graph

Notes:
- Deliberately went Python-first instead of all languages at once — proved
  the architecture end-to-end before expanding scope. Right call: hit and
  fixed a real version-compat issue (see below) that would've been more
  confusing to debug across multiple language integrations at once.
- KNOWN ISSUE (fixed): `tree-sitter-languages==1.10.2` is incompatible with
  `tree-sitter>=0.22` — the `Language.__init__` signature changed and
  breaks `get_parser()`/`get_language()` with
  `TypeError: __init__() takes exactly 1 argument (2 given)`.
  Fix: pin `tree-sitter==0.21.3` + `tree-sitter-languages==1.10.2` together
  in requirements.txt. Do not upgrade either independently.
- Verified live against plant-disease-detector's app.py: correctly
  extracted function name, exact line range, params, and all imports with
  line numbers via GET /api/projects/{id}/parse.
- Next language to add (when we get there): JavaScript/TypeScript, since
  tree-sitter-languages already bundles that grammar — same pattern as
  python_parser.py, new file js_parser.py.

---

## Phase 3 — Indexing (retrieval layer)
✅ Chunking strategy — chunk by function/class, not fixed token windows
✅ Generate embeddings for each chunk — using free local model
   (sentence-transformers, all-MiniLM-L6-v2), NOT a hosted/paid API
✅ Store in vector DB (Chroma, local/file-persisted), keyed to project_id +
   file path + line range
⬜ Store the dependency graph separately (file→file imports, function→
   function calls) — deferred, semantic search alone is working well for
   now; revisit when codebase chat needs multi-hop questions like "what
   depends on X"
⬜ Hybrid retrieval (semantic + graph) — depends on the graph above

Notes:
- Went with sentence-transformers over a hosted embedding API specifically
  to avoid Anthropic API costs during development — zero marginal cost,
  fully offline after first model download (~90MB, one-time).
- Verified live: natural-language query "load the model" correctly
  matched the `load_model()` function in app.py by MEANING, not keyword
  overlap — confirms semantic search is actually working, not just
  returning things that share words with the query.
- KNOWN ISSUE (fixed): chromadb's delete_collection() raises different
  exception types across versions for "collection doesn't exist"
  (NotFoundError in our installed version, not ValueError as docs/older
  examples suggest). reset_project_index() now catches bare Exception to
  cover this across versions.
- Chunk count was lower than expected on first real test (1 chunk for 2
  files) — worth a quick sanity check later: confirm train_plant.py
  actually only has 1 parseable function, vs. the parser silently missing
  something. Not blocking, just a flag for later.
- Terminal gremlin (not a code issue): PowerShell was glob-expanding
  --reload-exclude "data/*" into a literal matched path (data\repos),
  breaking the uvicorn command. Fixed by using --reload-dir app instead of
  an exclude pattern — sidesteps the glob entirely. Use --reload-dir app
  as the standard way to run the dev server from now on.

---

## Phase 4 — Codebase Chat (RAG Q&A)
✅ Basic RAG: question → retrieve relevant chunks (Phase 3 search) → LLM
   answers grounded ONLY in retrieved context, with file:line citations
⬜ Multi-hop questions ("which files depend on X") — needs the dependency
   graph from Phase 3, still deferred
⬜ Conversation memory within a session — currently stateless, each
   question is independent. Fine for now; add if follow-up Qs feel broken
⬜ Test against a real medium-sized open source repo — only tested against
   our small 2-file test repo so far; worth trying something bigger once
   JS/multi-language support exists, to see how retrieval holds up at scale

Notes:
- Built a pluggable LLM client (app/core/llm_client.py) that talks to
  local Ollama — zero API cost, fully offline. Interface is generate(),
  intentionally shaped so an AnthropicClient with the same interface can
  be swapped in later via config, without touching chat_service.py.
- Using llama3.2:1b (already pulled locally) — small/fast but not the
  smartest model. Good enough to prove the architecture works; swap to a
  bigger local model (qwen2.5-coder, llama3.1:8b) later if answer quality
  needs to improve and the machine can handle it.
- System prompt explicitly instructs the model to answer ONLY from
  retrieved context and say so if it can't — this is what keeps answers
  grounded instead of hallucinated. Worth stress-testing later: ask it
  something the repo genuinely doesn't cover, confirm it admits it
  doesn't know rather than making something up.
- VERIFIED LIVE: asked "How is the model loaded in this project?" against
  the real test repo. Got back an accurate, concise answer correctly
  citing load_model() in app.py lines 17-18 — not a vague/generic answer,
  genuinely grounded in the actual retrieved code.
- Known gotcha (user error, not a bug): Swagger UI's parameter field can
  end up containing the placeholder label text itself (e.g.
  "project_id: 9e626a75ee59") if typed carelessly — causes a 404 since
  it searches for a project with that literal string as its id. Always
  make sure path params contain ONLY the raw value.

---

## Phase 5 — Bug / Issue Detection
✅ Pattern-based static checks (no LLM): hardcoded secrets/keys, SQL built
   from dynamic strings, eval/exec, os.system/shell=True, pickle/yaml.load,
   weak hashes, verify=False, debug=True, bare except, mutable defaults
⬜ Layer LLM-based review on top for things static analysis misses (logic
   bugs, bad error handling, race conditions) — feed it function + its
   callers/callees from the graph for context
✅ Output format: file, line, severity, explanation, suggested fix, source
   line (GET /api/projects/{id}/scan, optional ?min_severity=)
✅ De-dupe and rank findings (sorted high→low; SEC001+SEC002 on the same
   line collapse into one)
⬜ Null-deref / taint-style checks — not attempted; rule-based detection of
   these is noisy, better left to the LLM pass

Notes:
- Uses Python's stdlib `ast` for Python rules (exact, zero dependencies).
  tree-sitter stays as the cross-language layer — JS/Java rules will use it.
- Tested on a hand-written sample with 13 planted issues: all caught, and
  the two decoys (parameterized SQL query, "your_token_here" placeholder)
  were correctly NOT flagged.
- Live endpoint verified two ways: plant-disease-detector -> 200 OK, 0
  findings (clean repo, expected); uploaded vulnerable_demo.py -> real
  findings returned (hardcoded key, hardcoded db_password, etc.), sorted
  high -> low. Pattern layer confirmed end to end.
- GOTCHA (fixed): .gitignore patterns containing a slash (e.g.
  `data/repos/*`) are anchored to the repo ROOT. Our data folders live in
  backend/data/, so they were NOT ignored and got committed (Chroma index
  .bin files, cloned test repos as embedded-repo gitlinks, uploaded test
  files). Fix: add backend/data/... patterns and `git rm -r --cached` the
  already-tracked paths. Rule of thumb: if a gitignore rule "isn't
  working", check whether it's anchored to the wrong directory.
- Known limits: rules are pattern-based, so expect some false negatives
  (e.g. SQL string built in one variable and executed later) and the
  occasional false positive. That's the trade for being fast, free and
  reproducible. The LLM pass is meant to cover the gaps.

---

## Phase 6 — Test Generation
✅ Given a function, generate pytest cases: normal, edge, invalid input,
   boundary (POST /api/projects/{id}/generate-tests) — verified live with
   the real llama3.2:1b on pricing_demo.py::calculate_price
⬜ Language-specific test scaffolding (jest, junit) — Python/pytest only
⬜ Methods inside classes — only top-level functions supported for now
✅ Run the generated tests and split into passing / failing (verify=true) —
   verified live: 200 OK, verified=true, 5 passing, 0 failing, no run_error
⬜ Coverage-aware: prioritize untested functions using existing coverage data
   if available

Notes:
- New files: app/testgen/generator.py (extract function with stdlib `ast`,
  build prompt, clean model output, assemble test file) and
  app/testgen/runner.py (run pytest in a subprocess, parse results).
- Verified offline with fake model replies (24 checks, all passing):
  function extraction incl. decorators; markdown-fence cleanup (closed,
  unclosed, chatter around it); model's redundant import dropped; wrong
  expectations land in `failing`; a parametrized test with ONE bad case
  counts as failed; passing-only file re-runs fully green; invalid-syntax /
  empty / no-tests replies give clear errors; path traversal rejected;
  un-importable module and timeouts give a run_error instead of crashing;
  parent-shell secrets are not visible to the test process.
- Bug found by those checks and fixed: pytest truncates its one-line
  failure summary to terminal width, and our long temp path cut the reason
  off, so every failure said "test failed". Fix: COLUMNS=250 for the
  subprocess + fallback to the first `E ...` line of each failure block.
- DESIGN DECISION: a failing generated test means EITHER the test is wrong
  OR the function has a real bug. We can't tell which automatically, so the
  API returns the failure reason and says so. Don't silently discard them
  and don't present them as bugs either.
- SAFETY (until Phase 8's Docker sandbox): verify=true imports and runs
  code from the ingested project plus LLM-written code on the host. It is
  OFF by default; only enable it for projects you trust. In place now:
  separate process, 30s timeout, minimal environment (no API keys/secrets
  passed through).
- Limitation: modules that need uninstalled packages or run code on import
  (e.g. Streamlit apps like plant-disease-detector's app.py) can't be
  imported, so verification reports run_error. Pure functions work best.
- Expect the 1B model to write some wrong tests — that's exactly what the
  verify step filters. A bigger local model (qwen2.5-coder:7b) would help.
- LIVE RESULTS: two real runs on calculate_price gave very different
  output. Run 1 (verify=false) contained several wrong assertions (e.g.
  expecting calculate_price(50) == 47.80 with no discount, and expecting
  calculate_price(0) to raise). Run 2 (verify=true) produced 5 tests that
  all passed. Same model, same function — quality varies run to run, which
  is the argument for always verifying.
- LIMITATION OF VERIFICATION: "passes" != "good test". Run 2's
  normal-input test computed its expected value with the same formula as
  the function, so it could never catch a wrong formula (a tautology).
  Mitigation so far: prompt now says to use hand-computed literal expected
  values. NOT mitigated: the model may still ignore that. Future idea:
  mutation check — tweak the function (e.g. flip an operator) and keep
  only tests that then FAIL; tests that still pass are worthless.

---

## Phase 7 — Fix Generation & Patching
✅ Given a scanner finding, propose a minimal patch (POST /propose-fix)
✅ Diff format: unified diff, applies with `git apply` (checked)
✅ Explain why the fix works, plus warnings about side effects
✅ Never writes to the project — returns a diff, a human applies it

How it works:
- We compute the diff ourselves (difflib). The model is never asked for a diff.
- Deterministic fixes (no LLM): BUG001 bare except, SEC007 weak hash,
  SEC008 verify=False, SEC009 debug=True.
- Everything else (SEC001-006, BUG002): the LLM rewrites only the enclosing
  function/statement; missing stdlib imports are added automatically.
- Checks before we call a patch `verified`: parses, function name + params
  unchanged, original finding gone, no new finding types introduced.
  Failed proposals are still returned with the failed checks listed.

Notes:
- `verified` means the PATTERN is gone, not that behavior is correct. That
  needs tests running against the patch = Phase 8.
- SEC007 fix changes hash output (old stored hashes won't match).
- Fix quality from a 1B model will vary; a bigger local model helps here too.

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