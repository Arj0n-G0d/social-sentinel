# Social Sentinel

A runnable Python/FastAPI prototype for a **synthetic** social-media knowledge graph and explainable behavioral review scores. The application collects Bluesky activity, classifies content and interaction context, writes Neo4j, and calculates user scores. Synthetic fixtures support offline testing.

Flow: deterministic synthetic data → configurable LLM classification → Neo4j properties → transparent risk scoring → API profiles and graph neighborhoods. Ingest stores the raw graph first; analysis adds validated classification properties. Scores use only the actor's own behavior. Graph connectivity is never a scoring feature.

## Setup

Requires Python 3.11+ and Docker with Docker Compose v2. Run commands from this directory.

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Set `NEO4J_PASSWORD` in `.env` to your own local password. The example value is only a placeholder. Compose and Python both read this file. Never commit `.env`.

Start Neo4j, wait for its HTTP health check, then start the API:

```sh
docker compose up -d --wait
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Neo4j Browser: <http://localhost:7474> (user `neo4j`, password from `.env`). Swagger UI: <http://localhost:8000/docs>. The app creates uniqueness constraints on startup and fails startup if Neo4j is unavailable. Use one Uvicorn worker for the prototype: a process-local lock serializes ingest/analysis. Graph data persists in the Compose volume.

Stop Neo4j while retaining data:

```sh
docker compose down
```

## Classifier configuration

The default `LLM_PROVIDER=mock` is **deterministic fixture replay, not an LLM classifier**. It exists solely to run the workflow without credentials. Mock classifications, API analysis summaries, and evidence identify simulated results through `provider: "mock"`, the model identifier, and a `MOCK` explanation. Unknown or changed fixture text returns `BENIGN` with all four probabilities set to 0 and an explanation identifying the missing fixture; it contributes no score. Expected labels are fixture ground truth for tests, not model predictions or accuracy measurements.

For real classification, edit `.env` and restart the API:

```dotenv
LLM_PROVIDER=gemini
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta
LLM_API_KEY=your-api-key
LLM_MODEL=gemini-flash-latest
ANALYSIS_VERSION=v1
LLM_TIMEOUT=45
```

The Gemini adapter appends `/models/{LLM_MODEL}:generateContent`, authenticates with `X-goog-api-key`, and requests JSON output. For Chat Completions, use `LLM_PROVIDER=chat_completions`, set `LLM_BASE_URL` to a compatible API root, and choose a model supporting JSON object mode. The primary classifier in real mode is the configured LLM; there are no keyword rules and no silent fallback to mock. Real mode requires a nonempty key and model.

The adapter requests JSON, validates all fields with Pydantic, and stores the valid raw JSON, parsed properties, provider/model, version, fingerprint, and status. Timeout, HTTP failure, malformed JSON, refusal, or invalid fields produce a `BENIGN` placeholder with all four probabilities set to 0 and `status=error`; they are retried by the next `/analyze`. Successful probability results are cached; error placeholders are not safety judgments. Secrets and provider error bodies are not returned.

The fingerprint covers the text, immediate parent/target and target-parent context, provider endpoint, provider/model, prompt, schema, and analysis version. Unchanged successful analysis is reused; model/context/version changes invalidate it. Expected labels and user identity/graph associations are never sent to the real model. For reproducibility, pin a provider model snapshot when available. Bump `ANALYSIS_VERSION` when intentionally changing analysis semantics.

The JSON request contract follows the [official structured-output documentation](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat). JSON object mode is used for compatibility; Pydantic performs schema validation. Neo4j uses [parameterized driver queries](https://neo4j.com/docs/python-manual/current/query-simple/).

## Run the full workflow

With the application running in another terminal:

```sh
# Optional: inspect reproducible raw activity (users, contents, interactions).
python -m scripts.generate_demo_data > /tmp/social-sentinel-demo.json

curl -sS -X POST http://localhost:8000/ingest-demo
curl -sS -X POST http://localhost:8000/analyze
curl -sS http://localhost:8000/users
curl -sS http://localhost:8000/users/u01
curl -sS http://localhost:8000/users/u02
curl -sS http://localhost:8000/users/u03
curl -sS http://localhost:8000/users/u04
curl -sS http://localhost:8000/users/u05
curl -sS http://localhost:8000/users/u14
curl -sS http://localhost:8000/users/u20
curl -sS http://localhost:8000/graph/u05

# Or run the workflow and print representative profiles plus a graph:
python -m scripts.demo
```

`generate_demo_data()` returns an `ActivityBatch` containing only users, contents,
and interactions. Expected probabilities are supplied separately by
`generate_demo_labels()` for mock replay and tests; they are not part of activity
exports or ingestion.

There are 20 fictional users, 39 posts/comments, and 42 separate likes/dislikes/shares. Ingest uses `MERGE` and a transaction, so repeating it does not duplicate nodes or edges and retains prior classification properties. Changing the provider from mock to real and calling `/analyze` replaces fixture analyses because fingerprints differ. A fresh full analysis makes 81 calls, performed sequentially. Real model results can differ from expected fixture labels.

| Endpoint | Result |
| --- | --- |
| `POST /ingest-demo` | Deterministic fixture counts; raw graph stored |
| `POST /analyze` | Analyzed/cached/error/benign counts and classifier provenance |
| `GET /users` | User score summaries, category scores/levels, review flags, completion state |
| `GET /users/{user_id}` | Profile, score breakdown, evidence, exclusions and contributions |
| `GET /graph/{user_id}` | Nodes/edges in a bounded outgoing neighborhood, plus immediate parent context |

User score summaries also expose `contains_mock_analysis` (derived from provider) and `analysis_sources`, so list results retain provenance even without the full evidence. Unknown user IDs return 404. Database failures return 503. Before successful analysis, zero scores mean **no scored evidence yet**; `analysis_complete=false` identifies incomplete results. The neighborhood returns at most 80 outgoing paths of depth two and 80 parent paths; it is deliberately a small view rather than a full graph export.

## Scoring and safeguards

The provider returns four independent probabilities between 0 and 1:

```json
{"probabilities": {"HATE": 0.91, "HARASSMENT": 0.72, "VIOLENCE": 0.12, "SPAM": 0.03}}
```

The application derives labels at **probability ≥0.5** for each category. This
example becomes `["HATE", "HARASSMENT"]`. When all four probabilities are below
0.5, the label is `["BENIGN"]`. Probabilities need not sum to one. The threshold
is defined by `CATEGORY_THRESHOLD` in `app/schemas.py` and included in cache
fingerprints. No separate benign probability is requested.

Provider JSON must include all four probabilities and must omit category labels,
severity, and overall confidence. An optional `explanation` string is accepted,
so a fine-tuned model can return only the probability object above. The current
Gemini and Chat Completions adapters use this same contract. A replacement
provider implements `classify(context)` and returns a validated `Classification`
and its raw response. No fine-tuned endpoint is configured yet.

The message's own text determines its category; parent and target text supply
context. Restart the application and run `POST /analyze` after updating. Database
initialization clears legacy analyses containing severity, overall confidence or
intent, and changed fingerprints trigger fresh analysis. The summary reports
`benign` for successfully analyzed items with no probability meeting the threshold;
errors are counted separately.

All scoring lives in `app/risk_scoring.py`. For an eligible item:

```text
points_for_category = 36 × probability_for_category × role_weight
role_weight = 1.0 for authored posts/comments, 0.75 for contextual harmful shares
category_score = min(100, sum(points from all eligible items in that category))
overall_score = max(hate, harassment, violence, spam)
```

Eligibility requires successful analysis and a category probability ≥0.5. Likes and dislikes contribute zero. Comments and shares
are evaluated from their own classification. No score is copied between users.


Each eligible post, comment, or share contributes separately, including multiple
actions in the same thread. Repeated copies of the same content or interaction
ID count once. Scores are calculated from the current database snapshot.

Evidence includes original text, explanation, four category probabilities,
provenance, candidate points, contributions, and exclusion reasons. Category
totals cap at 100; overall is the highest category score. There is no incident
grouping or parent-thread traversal in scoring.

Thresholds: 0–24 Low, 25–49 Moderate, 50–74 High, 75–100 Critical. A score ≥25 sets `flag_for_review=true`. These are **demonstration thresholds, not validated moderation standards**; a flag requests human review and is not a conclusive judgment. Fictional names and the fictional Zorvian ethnicity are synthetic testing constructs.

Demo reactions use probabilities below 0.5 for all categories and contribute no
risk. The mock model version is `fixture-replay-v5`, invalidating older cached
mock results.

## Example output (mock only)

The checked-in [mock preview](examples/mock_preview.json) is generated by:

```sh
python -m scripts.mock_preview
```

This offline command tests fixture classification/scoring without Neo4j and without any LLM calls. Use `scripts.demo` for the actual Neo4j/API flow.

| User | Scenario | Mock overall | Category | Review level |
| --- | --- | ---: | --- | --- |
| u01 | Four harmful authored posts | 100 | HATE | Critical |
| u02 | Literal threatening comment | 34.2 | VIOLENCE | Moderate |
| u03 | Counter-speech comment | 0 | All zero | Low |
| u04 | Only likes a harmful post | 0 | All zero | Low |
| u05 | Warning share condemning harm | 0 | All zero | Low |
| u14 | Explicitly promotes hateful post | 25.65 | HATE | Moderate |
| u20 | Benign author with many connections | 0 | All zero | Low |

These numbers are fixture-derived examples, **not observed LLM predictions**. Real provider output is probabilistic and requires evaluation; the automated safeguards still apply to its validated classifications.

## Streaming suspicious users

The live collector uses Bluesky Jetstream v2 WebSockets. It resolves the initial
accounts once, fetches their recent posts and direct comments/likes/reposts once,
then processes live events. Only the original seeds receive HTTP backfill.
Discovered accounts are monitored from stream events without fetching their
existing history. No Bluesky HTTP activity calls run after the socket starts.

```sh
python -m scripts.stream_social --seed alice.bsky.social bob.bsky.social --max-users 100
# Initial collection only, without opening a WebSocket:
python -m scripts.stream_social --seed alice.bsky.social --once
```

The old `scripts.poll_social` command forwards to this same streaming command;
`--interval` has been removed. `--page-size` (1–100, default 25) and `--max-pages`
(default 2) bound the initial seed HTTP fetches. `--max-users` caps the pool including
seeds. Direct activity is still emitted when an actor cannot enter the full pool.
Use Ctrl+C to stop.

The subscription requests `app.bsky.feed.post`, `app.bsky.feed.like`, and
`app.bsky.feed.repost` across all authors. Filtering only pool DIDs would miss
unknown participants. The worker filters locally: keep top-level posts from pool
members, comments whose immediate parent is a watched post, and likes/reposts
whose target is a watched post. Replies to comments and reactions to comments are
ignored. Pool membership does not classify an account as harmful.
See the [official Jetstream protocol](https://bsky.network/docs/jetstream/).

Canonical `User`, `Content`, `Interaction`, and `pool_member` records are printed
as JSON lines. Live participants initially use their DID as their display name;
initial activity fetches can supply their handle. Edits are emitted again;
unchanged records are suppressed by a bounded cache. Observed deletions emit
`{"type":"delete","record_type":"Content","record_id":"at://..."}` (or
`Interaction`). A reaction deletion can be identified only after its native
record URI has been observed on the stream. Initial repost timestamps are empty
because the HTTP listing omits them; stream records contain their creation time.

The worker reconnects with exponential backoff and an in-memory sequence cursor.
Replay starts just before the initial fetch and resumes after disconnects, within
Jetstream's replay window. Inclusive replay is deduplicated. Pool, watched posts,
stream record mappings and cursor reset on restart; no durable checkpoint is
implemented. Long disconnects outside the replay window can leave gaps. Initial
HTTP failures are logged without stopping live monitoring; they are not retried
automatically. Initial listings and thread responses may be truncated, so old
posts outside the initial window and events predating admission can be missed.
Pool size and the deduplication cache are bounded; watched-post and stream record
indexes grow with the relevant activity during a run.

Collection makes no database writes or LLM calls. Output remains compatible with
the existing canonical activity models; deletion events are output notifications.

Seed handles, @handles, and DIDs are accepted by `resolve_user`.
`User.user_id` stores the native DID (for example, `did:plc:...`), ready to pass
directly to the API. `poll_user` uses it without resolving or fetching the profile
again. Discovered users also store native DIDs.
`Content.content_id` stores the native `at://...` URI, `Content.user_id` stores
the author DID, and `parent_id` and interaction `target_id` store native post
URIs. These IDs can be passed directly to the API without stripping prefixes.
The available adapter is `app/platforms/bluesky.py`; Reddit and X adapters are not
present in this checkout. `--platform bluesky` is optional.

## Tests

```sh
python -m pytest -q

# Opt-in real Neo4j integration: use a dedicated local/demo database.
# MERGEs demo IDs, does not delete nodes, and leaves demo data in place.
RUN_NEO4J_TESTS=1 python -m pytest tests/test_neo4j.py -v
```

Default tests use a test-only in-memory store, fixture provider, and mocked HTTP transport. They cover the requested behavioral safeguards, validation, context and cache invalidation, retries, scoring explanations, idempotent ingestion, API requests, and the real-provider request contract. They do not make paid LLM calls. The opt-in test verifies actual Neo4j constraints, Cypher ingestion, stored analyses, idempotency, and graph serialization. No production in-memory storage mode is included.

## Files

- `app/config.py`, `schemas.py`: environment settings and validated records.
- `app/demo_data.py`: deterministic synthetic dataset and expected labels.
- `app/llm_classifier.py`, `workflow.py`: provider interface, context, classification and cache.
- `app/graph_store.py`: three node labels, four relationship types, constraints and parameterized Cypher.
- `app/risk_scoring.py`: formula, thresholds, deduplication and evidence.
- `app/main.py`, `routes.py`: application lifecycle and the five requested endpoints.
- `scripts/`: data export, full API demo and clearly identified offline mock preview.

This is a local demonstration intended for synthetic data. No authentication, distributed job runner, or production moderation policy is implemented.

## Verification in this workspace

The Python 3.13 test run completed with **65 passed, 1 skipped**. The skipped test requires live Neo4j. Docker is not installed in this workspace, so Compose startup and live graph queries were not exercised. No real LLM calls were made; the HTTP contract was tested with a mock transport. The generated example is fixture replay only.

## Run collection, classification and scoring together

Configure Neo4j and your classifier in `.env`, then run:

```sh
python -m app.testing alice.bsky.social bob.bsky.social --analysis-interval 60 --max-users 100
# One initial collection/classification/scoring pass without streaming:
python -m app.testing alice.bsky.social --once
```

The runner initializes Neo4j and ingests initial users, posts, direct comments,
likes and reposts. It runs Jetstream on the main thread; new records and
edits are ingested continuously, and observed deletions are applied to the DB.
Deleting content also deletes interactions targeting it; surviving comments are
retained and their changed context is reconsidered at the next analysis cycle.

Classification/scoring runs in a daemon background thread. Later cycles
wait `--analysis-interval` seconds after the previous cycle finishes, so cycles
do not overlap. Each cycle reads the database and reuses successful analyses with
matching fingerprints. New or edited content, changed parent/target context,
provider/model/schema changes and failed analyses trigger fresh classification.
Model calls run without holding the ingestion lock. Results for records edited
or deleted during inference are discarded (`outdated`) and reconsidered next cycle.

After classification, the runner reads a fresh snapshot, gathers each user's own
Content and Interactions, calls `score_user`, and persists the resulting summary
on that User. Probabilities whose fingerprints no longer match are excluded from
that scoring pass, and the user's `analysis_complete` is false. Stored properties
include `overall_score`, `review_level`, `flag_for_review`, four
`category_score_*` values, and `scores_updated_at`. `score_summary` stores the full
summary as JSON because Neo4j properties cannot contain nested maps. Snapshots
and graph output decode it back to ordinary summary fields. Evidence remains on
content/interaction records and is calculated on demand.

Short database operations share a lock inside this runner. This is not a
cross-process lock; run one pipeline writer for this workflow. Stream failures propagate directly on the main thread. There is no stop event,
error queue, or stream-health checker. Ctrl+C exits the runner and closes the
platform and database resources; background classification is not joined on exit.
An uncaught classification error ends its background thread and is reported by
Python; no automatic supervisor restarts it.
The API continues to compute scores on demand; stored scores are the latest
periodic snapshot, not an immediate update after every event.

`--page-size` and `--max-pages` bound initial fetches. The default `mock` provider
only replays synthetic fixtures; live input requires a configured real provider
for meaningful predictions. The standalone `scripts.stream_social` command stays
a collection-only JSON output tool. Periodic DB writes and analysis are enabled
by `app.testing`.


## Training dataset

The combined Jigsaw/Enron dataset is available in
[dataset/combined.csv](dataset/combined.csv), with the exact columns
`text,hate,harrasment,violence,spam`. See [dataset/README.md](dataset/README.md)
for the label mappings and build command. All target columns contain only `0` or `1`.
