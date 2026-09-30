# CLAUDE.md

Tectonic Hackathon, Leuven, 30 September 2026. SD Worx track. Team of 2 to 4.
Public repo: judges read this file. Source for challenge and rules: the
Participants Guide (PDF, not in the repo). Page numbers below refer to it.

## Challenge

SD Worx, "Unlock the Knowledge Within. Find it. Understand it. Trust it." (p. 5)

- Question: "How might we turn fragmented organisational knowledge into a trusted shared resource?"
- Task: "Build a focused proof of concept that makes organisational knowledge easier to find, trust or share. Choose one meaningful problem. You do not need to solve everything."
- The questions behind it: "What is reliable? What is current? What applies in this context? Where are the gaps? Who has relevant expertise? Which answer should a person trust?"
- Inspiration areas, "not a checklist": Trust (is it relevant and reliable), Capture (knowledge beyond inboxes, documents and siloed teams), Detect (conflicting, duplicated, missing or outdated knowledge), Connect (find the right expertise when documents are not enough).
- Scope: "Focus on one role, one workflow, one knowledge source or one trust signal. Make the moment of doubt tangible." Move someone from "I found something" to "I understand why I can rely on it".
- "Do not start with prescribed technology." Do not "hide complexity behind a black box": make trust "visible, explainable and useful".
- Example friction (p. 5): an AI assistant returns three documents (one recently updated, one without an owner, one for another country) and a Teams thread contradicts them. A payroll consultant inherits a client portfolio with knowledge spread over documents, chats, workflows, applications and experts.
- Users: SD Worx employees working on HR, payroll and workforce services across Europe.
- TODO: whether SD Worx provides data, APIs or sample documents. The guide names none.

## How we are judged

Weights from the BuilderBase track dashboard. The guide (p. 11) lists the same four without weights, and calls the first "Creativity".

- Originality 30%: a clear angle on one moment of doubt, not a generic "chat with your docs" bot.
- Technical Ability 30% ("does it work?", p. 11): the demo flow has to run live, end to end.
- Fit to the case challenge 30%: tie every feature to a quoted challenge question above.
- Security 10%: connect the repo to Aikido early, run the AI Code Audit for a baseline, fix, mark resolved (p. 6 to 7). It checks business logic flaws, IDOR, authentication and authorization, so every data access needs a permission check.

## Deadline and submission

- Deadline: 22:30 CEST per the event page, 23:00 CEST per the BuilderBase dashboard. Treat 22:30 as hard and submit by 22:15.
- TODO: event schedule. The guide has none.
- Submit on BuilderBase (p. 11): demo video under 3 minutes, written description, repo link (`https://github.com/lowie27/hackaton`, already submitted), Aikido screenshots "before and after" (p. 7).
- Rules (p. 12): one project per team, no changes after the final submission, repo stays public until judging ends, include a short README (what it is, how to run it, what is unfinished), no passwords, keys or confidential data.

Last 45 minutes (from 21:30):

- [ ] Code frozen and pushed
- [ ] Repo public, README says what it is, how to run it, what is unfinished
- [ ] Aikido before and after screenshots taken
- [ ] Demo video recorded, under 3 minutes
- [ ] Description written
- [ ] Submitted on BuilderBase by 22:15

## Working rules for agents

- Finish one demo-able flow end to end before adding breadth.
- Say so when a request risks the deadline, and name what it would push out.
- Never commit secrets, tokens or the Google Cloud team code. Keep them in `.env`, which `.gitignore` excludes.
- Follow the `propose-commit` skill for every commit.

## Idea

Direction so far: a trusted knowledge layer for SD Worx employees. Each feature maps to a challenge question (p. 5):

- Hybrid search that explains itself. Every hit shows why it matched (BM25 terms and rank, vector similarity and rank) and its owner, country, last update and groups. Answers "What applies in this context?" and "Which answer should a person trust?"
- Near-duplicate alerts on upload. The uploader, the other document's uploader and the group managers get notified. Covers the "Detect" inspiration area (duplicated, outdated knowledge).
- Access control. Users only find documents shared with their groups. This is also what the Aikido audit checks (p. 6).
- Context-aware ranking that explains itself (`src/kb/context.py`). Documents carry country, location, department, language, tags, a validity period (`valid_from`/`valid_until`) and a snapshot of the uploader's position. Users have a profile (country, location, department, position). Results that apply to the user move up, and each hit lists reasons ("applies to your country (BE)", "official policy document") and warnings ("expired on 2024-12-31", "no accountable owner", "applies to NL, you work in BE"). Answers "What is current?", "What applies in this context?" and "Which answer should a person trust?". This is the p. 5 example friction (recently updated / no owner / other country) made visible.
- Where results disagree (`src/kb/conflicts.py`): the "moment of doubt" of the demo. Facts are extracted from the results and compared; the UI shows 92% / 93% / 85% side by side with the quoted sentences and says which to trust and why. Covers "Detect: conflicting knowledge" and "Which answer should a person trust?".

## Status

Working and tested (62 tests, `pytest` against Postgres with pgvector):

- `python -m kb init | seed | search | notifications | ingest | embed`. `seed` loads demo users (anna, bram, noor, admin at example.com), groups and the synthetic documents in `data/sample/`.
- Search filters (`SearchFilters`, CLI `--country --department --source --language --tag --valid-on`) run in SQL before top-k and under RLS.
- Demo flow: `python -m kb search "double holiday pay" --as bram@example.com` ranks the 2025 policy first, flags the Teams copy (informal, no owner) and the 2019 FAQ (expired, 7 years old).
- Demo flow: Bram searches and sees only payroll-be documents. A Dutch query from Noor finds the NL document. Bram uploads a Teams copy of the 2025 policy with 93% instead of 92%, and Anna and Bram get notified (89% word overlap, 99% meaning).

Not done:

- No API to dismiss or resolve an alert. Notifications are database rows only (no email or Teams).
- Text search uses the `english` config only; the multilingual vector model covers other languages.
- Index views are rebuilt in full on every upload, and duplicate detection scans every document. Fine at demo scale.
- The first run downloads the embedding model (about 6 minutes on the event network). Run `python -m kb seed` before the live demo.

## Code rules

- Anything done on behalf of a user runs inside `kb.db.user_session`, which switches to the `kb_app` role so row-level security applies. Never query user-facing data on the owner connection.
- The only owner-connection read for a user request is that user's own profile (`Retriever._user_context`), keyed by the authenticated id.
- Uploader position, department and manager flag are copied from `kb.users` at upload time, never taken from document metadata.
- Every write path calls a check in `src/kb/rbac.py` first. User ids come from the API's authentication, never from the request body.
- Notifications and upload results never name a document the recipient cannot read (`rbac.can_read`).
- SQL files in `sql/` are idempotent and applied in name order by `python -m kb init`. pgvector lives in `sql/optional/vector.sql` and is only applied when `KB_VECTOR_ENABLED=true`.
- Tests run in a transaction that is rolled back (`tests/conftest.py`). Vector tests use a hashing embedder, so they need no model download.

## Stack

- Knowledge layer (this repo, `src/kb`): Python 3.11+, psycopg 3, PostgreSQL 17 with pgvector (`docker-compose.yml`).
- Search: BM25 computed in SQL (`sql/001_bm25.sql`) and optional pgvector similarity (`KB_VECTOR_ENABLED`), fused with reciprocal rank fusion in Python, optionally reranked by a multilingual cross-encoder (`KB_RERANK_ENABLED`). Local multilingual embeddings via fastembed.
- Access control: groups with member/manager roles, enforced by Postgres row-level security (`sql/004_security.sql`). Write paths call checks in `src/kb/rbac.py`.
- Near-duplicate alerts on upload notify the uploader, the other document's uploader and group managers (`src/kb/alerts.py`).
- UI: built separately by a teammate on top of `kb.KnowledgeBase` (`src/kb/service.py`).
- Demo UI and API: FastAPI + a single static page in `src/kb/web/`, `docker compose up -d --build` (service `web`). Login is a demo user picker with an HMAC-signed cookie.
