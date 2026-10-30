"""Run initial ingestion, live collection, periodic classification and saved scores."""
import argparse
import logging
from threading import Lock, Thread
from time import sleep

from app.config import Settings
from app.graph_store import GraphStore
from app.llm_classifier import Classifier, GeminiProvider
from app.platforms.bluesky import BlueskyPlatform
from app.schemas import ActivityBatch, Content, Interaction, User
from app.streaming import StreamingWorker
from app.workflow import analyze_store, score_store

logger = logging.getLogger(__name__)


class DatabaseEvents:
    """Persist collection events while sharing short database locks with analysis."""
    def __init__(self, store, lock):
        self.store, self.lock = store, lock

    def __call__(self, event: dict):
        kind = event['type']
        if kind == 'delete':
            with self.lock:
                self.store.delete_record(event['record_type'], event['record_id'])
            return
        batch = ActivityBatch(users=[], contents=[], interactions=[])
        if kind == 'pool_member':
            batch.users = [User.model_validate(event['user'])]
        elif kind == 'User':
            batch.users = [User.model_validate(event['record'])]
        elif kind == 'Content':
            batch.contents = [Content.model_validate(event['record'])]
        elif kind == 'Interaction':
            batch.interactions = [Interaction.model_validate(event['record'])]
        else:
            raise ValueError(f'Unsupported collection event: {kind}')
        with self.lock:
            self.store.ingest(batch)
        if kind == 'Content':
            logger.info('Saved %s to DB: %s (author %s)',
                        batch.contents[0].content_type, batch.contents[0].content_id,
                        batch.contents[0].user_id)
        elif kind == 'Interaction':
            logger.info('Saved %s to DB: %s', batch.interactions[0].interaction_type,
                        batch.interactions[0].interaction_id)


def run_pipeline(platform, store, classifier, seeds, *, analysis_interval=60,
                 max_users=100, once=False):
    """Ingest existing activity, stream updates, and periodically refresh scores."""
    if analysis_interval <= 0:
        raise ValueError('analysis_interval must be positive')
    lock = Lock()
    worker = StreamingWorker(platform, seeds, max_users=max_users,
                             emit=DatabaseEvents(store, lock))
    worker.bootstrap()

    def classify_and_score():
        analysis = analyze_store(store, classifier, lock=lock)
        scored = score_store(store, classifier, lock=lock)
        logger.info('Analysis: %s; saved scores for %s users', analysis, scored)

    if once:
        classify_and_score()
        return

    def periodic_analysis():
        while True:
            classify_and_score()
            sleep(analysis_interval)

    Thread(target=periodic_analysis, name='classification', daemon=True).start()
    worker.run()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('handles', nargs='+', metavar='HANDLE',
                        help='Full account handles (e.g. alice.bsky.social) or DIDs')
    parser.add_argument('--platform', choices=['bluesky'], default='bluesky')
    parser.add_argument('--analysis-interval', type=float, default=60,
                        help='Seconds between completed classification/scoring cycles')
    parser.add_argument('--max-users', type=int, default=100)
    parser.add_argument('--page-size', type=int, default=25)
    parser.add_argument('--max-pages', type=int, default=2)
    parser.add_argument('--once', action='store_true', help='Ingest, classify and score once without streaming')
    args = parser.parse_args()
    if args.analysis_interval <= 0:
        parser.error('--analysis-interval must be positive')
    logging.basicConfig(level=logging.INFO)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    settings = Settings()
    platform, store = None, None
    try:
        provider = GeminiProvider(settings)
        classifier = Classifier(settings, provider)
        platform = BlueskyPlatform(page_size=args.page_size, max_pages=args.max_pages)
        store = GraphStore(settings)
        store.initialize()
        run_pipeline(platform, store, classifier, args.handles,
                     analysis_interval=args.analysis_interval, max_users=args.max_users,
                     once=args.once)
    except ValueError as exc:
        parser.error(str(exc))
    except KeyboardInterrupt:
        logger.info('Stopping collection')
    finally:
        if platform is not None:
            platform.close()
        if store is not None:
            store.close()


if __name__ == '__main__':
    main()
