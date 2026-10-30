# Base graph generation

Run from the repository root using the Neo4j settings in `.env`:

```sh
python -m base-graph-generation.base-graph-generator \
  --context 'gaza' --context 'ceasefire' \
  --since '2026-10-01T00:00:00Z' --until '2026-10-08T00:00:00Z' \
  --max-users 200 --max-posts 200 --max-interactions 500 \
  --max-requests 50 --page-size 25 --max-pages 2
```

```sh
python3 -m base-graph-generation.generate --context gaza --context rafah --context palestine --context ceasfire --since 2023-10-07T00:00:00Z --until 2023-11-01T00:00:00Z --neo4j-database gaza

python3 -m base-graph-generation.generate --context "us election" --context "trump" --context "harris" --context "presidential debate" --context "electoral college" --since 2024-10-15T00:00:00Z --until 2024-11-20T00:00:00Z --neo4j-database us-elections
```

`--neo4j-database` optionally overrides the configured database. Each context
is a separate Bluesky search query; matching results are combined and deduplicated.
The timeline includes the start and excludes the end. Both timestamps must have
timezones. Posts, direct replies, and likes are checked against their creation time
before storage. Users are included only for accepted content or likes.

Defaults cap each run at 200 users, 200 content nodes (including replies), 500 like
interaction nodes, and 50 API requests. Each search query and likes listing gets
at most two pages of 25 items. The request budget includes thread requests.
Search happens before enrichment; a small request budget may leave no capacity
for replies or likes. Set `--max-interactions 0` to disable likes.

The adapter is `app.platforms.bluesky.BlueskyPlatform`. Persistence uses
`app.graph_store.GraphStore.ingest`, preserving the existing User, Content,
Interaction, CREATED, REPLIES_TO, PERFORMED, and TARGETS model. Reply edges exist
when both endpoints were collected; missing ancestors are not fetched. Reposts
are omitted because their public listing does not provide creation timestamps.

Limits bound writes per invocation, not total database size. Repeated invocations
merge stable IDs and can accumulate new records. Search results are a bounded
sample, not an exhaustive historical archive. Collection completes before the
single ingest transaction; API failures propagate without ingesting a partial
sample. Resources created by the generator are closed; injected ones remain open.

For Python callers (the requested module path contains hyphens):

```python
from importlib import import_module

BaseGraphGenerator = import_module(
    'base-graph-generation.base-graph-generator'
).BaseGraphGenerator
result = BaseGraphGenerator(['gaza']).generate(
    '2026-10-01T00:00:00Z', '2026-10-08T00:00:00Z', max_posts=100
)
```

Initialize the normal application schema before concurrent ingestion. This
command does not call `GraphStore.initialize()`, which also clears legacy
analysis fields. It never clears existing data or runs classification.

## Search returns HTML 403 Forbidden

Anonymous search can be restricted by the Bluesky service. Configure an
app-password session in `.env`:

```dotenv
BLUESKY_HANDLE=your-handle.bsky.social
BLUESKY_APP_PASSWORD=your-app-password
```

Create the app password in Bluesky Settings → Privacy and Security → App Passwords.
Then run the current entry point from the repository root:

```sh
python -m base-graph-generation.generate \
  --context gaza --since 2026-10-01T00:00:00Z --until 2026-10-08T00:00:00Z
```

With both values present, the CLI logs in once through `https://bsky.social` and
uses the SDK's authenticated session for collection. Without credentials it keeps
using the public API. Credentials are never sent to the public AppView host.
The data-request budget excludes the initial login/session setup. Authentication
does not guarantee service availability; if authenticated search is still denied,
check service status and account/network access before retrying. Search 403 errors
now include configuration guidance instead of the HTML response.
