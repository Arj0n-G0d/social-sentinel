"""Generate an event graph: python -m base-graph-generation.generate."""
import argparse
import json
from datetime import datetime

from app.config import Settings
from app.graph_store import GraphStore
from app.platforms.bluesky import BlueskyPlatform


class BaseGraphGenerator:
    def __init__(self, context: list[str], neo4j_database: str, settings: Settings, platform: BlueskyPlatform):
        if not context or any(not isinstance(t, str) or not t.strip() for t in context):
            raise ValueError('context must contain non-empty search queries')
        self.context = list(context)
        self.graph_store = GraphStore(settings, neo4j_database = neo4j_database)
        self.platform = platform
        self.neo4j_database = neo4j_database

    def generate(self, since: str | datetime, until: str | datetime, *,
                 max_users: int = 200, max_posts: int = 200,
                 max_interactions: int = 500, max_requests: int = 50,
                 page_size: int = 25, max_pages: int = 2) -> dict:
        """Ingest one bounded sample in [since, until), merging stable IDs.

        Limits apply per run, not to the entire existing database. Collection
        finishes before writing. Injected resources remain owned by the caller.
        """
        start = datetime.fromisoformat(since.replace('Z', '+00:00')) if isinstance(since, str) else since
        end = datetime.fromisoformat(until.replace('Z', '+00:00')) if isinstance(until, str) else until
        if start.tzinfo is None or end.tzinfo is None or start >= end:
            raise ValueError('Provide timezone-aware timestamps with since < until')
        if (any(type(v) is not int or v < 1 for v in
                (max_users, max_posts, max_requests, max_pages, page_size))
                or page_size > 100 or type(max_interactions) is not int or max_interactions < 0):
            raise ValueError('Invalid collection limits')
        platform = self.platform or BlueskyPlatform(page_size=page_size, max_pages=max_pages)
        try:
            activity = platform.collect_context(
                self.context, start, end, max_users=max_users, max_posts=max_posts,
                max_interactions=max_interactions, max_requests=max_requests)
            store = self.graph_store or GraphStore(
                settings=self.settings or Settings(), neo4j_database=self.neo4j_database)
            try:
                # initialize() also invalidates legacy analyses, so do not call it here.
                counts = store.ingest(activity)
            finally:
                if self.graph_store is None:
                    store.close()
        finally:
            if self.platform is None:
                platform.close()
        return {'context': self.context, 'since': start.isoformat(), 'until': end.isoformat(),
                'counts': counts,
                'limits': {'users': max_users, 'contents': max_posts,
                           'interactions': max_interactions, 'requests': max_requests},
                'note': 'Bounded sample per run; search is not exhaustive. Reposts excluded.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context', action='append', required=True, help='Search query; repeat for alternatives')
    parser.add_argument('--since', required=True, help='Inclusive ISO timestamp with timezone')
    parser.add_argument('--until', required=True, help='Exclusive ISO timestamp with timezone')
    parser.add_argument('--neo4j-database')
    for name, default in [('max-users', 200), ('max-posts', 200), ('max-interactions', 500),
                          ('max-requests', 50), ('page-size', 25), ('max-pages', 2)]:
        parser.add_argument(f'--{name}', type=int, default=default)
    args = vars(parser.parse_args())
    settings = Settings()
    platform = BlueskyPlatform(
        page_size=args['page_size'], max_pages=args['max_pages'],
        handle=settings.bluesky_handle,
        app_password=settings.bluesky_app_password.get_secret_value())
    generator = None
    try:
        generator = BaseGraphGenerator(args.pop('context'), args.pop('neo4j_database'), settings, platform)
        print(json.dumps(generator.generate(**args), indent=2))
    finally:
        platform.close()
        if generator is not None:
            generator.graph_store.close()


if __name__ == '__main__':
    main()
