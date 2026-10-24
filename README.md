# Social Sentinel

A runnable Python/FastAPI prototype for a **synthetic** social-media knowledge graph and explainable behavioral review scores. One application generates fixtures, classifies content and interaction context, writes Neo4j, and calculates scores on demand. No platform integration, frontend, broker, or graph analytics service.

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

The default `LLM_PROVIDER=mock` is **deterministic fixture replay, not an LLM classifier**. It exists solely to run the workflow without credentials. Mock classifications, API analysis summaries, and evidence identify simulated results through `provider: "mock"`, the model identifier, and a `MOCK` explanation. Unknown or changed fixture text produces `UNCERTAIN`, never a keyword-based guess. Expected labels are fixture ground truth for tests, not model predictions or accuracy measurements.

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

The adapter requests JSON, validates all fields with Pydantic, and stores the valid raw JSON, parsed properties, provider/model, version, fingerprint, and status. Timeout, HTTP failure, malformed JSON, refusal, or invalid fields become zero-confidence `UNCERTAIN` with `status=error`; they are retried by the next `/analyze`. Complete uncertain results are cached. Secrets and provider error bodies are not returned.

The fingerprint covers the text, immediate parent/target and target-parent context, provider endpoint, provider/model, prompt, schema, and analysis version. Unchanged successful analysis is reused; model/context/version changes invalidate it. Expected labels and user identity/graph associations are never sent to the real model. For reproducibility, pin a provider model snapshot when available. Bump `ANALYSIS_VERSION` when intentionally changing analysis semantics.

The JSON request contract follows the [official structured-output documentation](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat). JSON object mode is used for compatibility; Pydantic performs schema validation. Neo4j uses [parameterized driver queries](https://neo4j.com/docs/python-manual/current/query-simple/).

## Run the full workflow

With the application running in another terminal:

```sh
# Optional: inspect the reproducible raw data and expected testing labels.
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

There are 20 fictional users, 39 posts/comments, and 42 separate likes/dislikes/shares. Ingest uses `MERGE` and a transaction, so repeating it does not duplicate nodes or edges and retains prior classification properties. Changing the provider from mock to real and calling `/analyze` replaces fixture analyses because fingerprints differ. A fresh full analysis makes 81 calls, performed sequentially. Real model results can differ from expected fixture labels.

| Endpoint | Result |
| --- | --- |
| `POST /ingest-demo` | Deterministic fixture counts; raw graph stored |
| `POST /analyze` | Classified/cached/error/uncertain counts and classifier provenance |
| `GET /users` | User score summaries, category scores/levels, review flags, completion state |
| `GET /users/{user_id}` | Profile, score breakdown, evidence, exclusions and contributions |
| `GET /graph/{user_id}` | Nodes/edges in a bounded outgoing neighborhood, plus immediate parent context |

User score summaries also expose `contains_mock_analysis` (derived from provider) and `analysis_sources`, so list results retain provenance even without the full evidence. Unknown user IDs return 404. Database failures return 503. Before successful analysis, zero scores mean **no scored evidence yet**; `analysis_complete=false` identifies incomplete results. The neighborhood returns at most 80 outgoing paths of depth two and 80 parent paths; it is deliberately a small view rather than a full graph export.

## Scoring and safeguards

Categories describe the topic: `HATE`, `HARASSMENT`, `VIOLENCE`, `SPAM`, `BENIGN`, or `UNCERTAIN`.
Intent describes the stance, using four values:

- `ENDORSEMENT`: supports, promotes, or directly expresses harmful behavior, including authored threats, abuse, and spam.
- `CONDEMNATION`: opposes harm, including warnings and counter-speech.
- `NEUTRAL`: ordinary conversation, factual reporting, or discussion without support or opposition.
- `UNCERTAIN`: insufficient evidence to determine stance; use low confidence. An unknown topic separately uses category `UNCERTAIN`.

For example, a warning against hate is category `HATE`, intent `CONDEMNATION`, and severity 0. A factual report of a threat is category `VIOLENCE`, intent `NEUTRAL`, and severity 0. A harmful category alone never increases risk.

After updating from the old labels, restart the application and run `POST /analyze` to replace stored classifications. The changed prompt/schema automatically invalidate the old cache. The analysis summary's `uncertain` count includes newly analyzed items with category or intent `UNCERTAIN`, or confidence below 0.65.

All scoring lives in `app/risk_scoring.py`. For an eligible item:

```text
points = 12 × severity × confidence × role_weight
role_weight = 1.0 for authored posts/comments, 0.75 for contextual harmful shares
category_score = min(100, sum(strongest points per distinct incident in that category))
overall_score = max(hate, harassment, violence, spam)
```

Eligibility requires successful analysis, confidence ≥0.65, a harmful category, positive severity, and intent `ENDORSEMENT`. Likes and dislikes always contribute zero, regardless of classifier output. Uncertain, condemnation, counter-speech, reporting, and neutral results contribute zero. A comment is evaluated from its own classification, not its parent's labels. A share must have contextual evidence of endorsement. No score is copied between users.

An incident is a root post/thread. Multiple comments or shares by the same user in the same thread/category count only the strongest item. Distinct root posts accumulate and therefore capture repetition. This conservative rule can undercount separate incidents inside a long thread; it is an explicit MVP tradeoff. Exact repeated ingestion of the same IDs never adds risk. Scores are calculated from a database snapshot when requested, not persisted as stale totals.

Evidence includes original text, classification explanation, intent, severity, confidence, provenance, incident ID, candidate points, actual per-category contributions, and exclusion reasons. If an eligible item has no contributions, a stronger or equal item already represents that incident/category. Totals cap at 100; the overall score uses the maximum category, so categories are not double-counted against each other.

Thresholds: 0–24 Low, 25–49 Moderate, 50–74 High, 75–100 Critical. A score ≥25 sets `flag_for_review=true`. These are **demonstration thresholds, not validated moderation standards**; a flag requests human review and is not a conclusive judgment. Fictional names and the fictional Zorvian ethnicity are synthetic testing constructs.

Demo reactions follow an explicit synthetic rule: likes copy all target categories and intent; dislikes copy all categories and swap `ENDORSEMENT` with `CONDEMNATION`. `NEUTRAL` and `UNCERTAIN` remain unchanged. Both reaction types have severity 1. User u20 likes and u18 dislikes each of the first 18 posts/comments, including counter-speech and neutral discussion. These are fixture assumptions; real LLM instructions remain conservative about reaction intent, and both reaction types still contribute zero risk. The mock model version is `fixture-replay-v3`, invalidating older cached mock results.

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
