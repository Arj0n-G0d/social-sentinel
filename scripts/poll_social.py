"""Run with: python -m scripts.poll_social --seed username --once"""
import argparse
import logging

from app.config import Settings
from app.platforms.reddit import RedditPlatform
from app.platforms.x import XPlatform
from app.platforms.bluesky import BlueskyPlatform
from app.polling import PollingWorker


def main():
    parser = argparse.ArgumentParser(description='Print canonical social activity as JSON lines')
    parser.add_argument('--platform', choices=['reddit', 'x', 'bluesky'], default='reddit')
    parser.add_argument('--seed', nargs='+', required=True, help='Seed usernames (space separated)')
    parser.add_argument('--interval', type=float, default=60, help='Seconds between polling cycles')
    parser.add_argument('--max-users', type=int, default=100)
    parser.add_argument('--max-depth', type=int, default=1, help='Discovery depth; seeds are depth 0')
    parser.add_argument('--page-size', type=int, default=25)
    parser.add_argument('--max-pages', type=int, default=2, help='Pages per user listing per cycle')
    parser.add_argument('--comment-limit', type=int, default=100, help='Requested thread comment limit')
    parser.add_argument('--thread-depth', type=int, default=2, help='Bluesky reply depth (0–100)')
    parser.add_argument('--once', action='store_true', help='Poll current seeds once and exit')
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    settings = Settings()
    try:
        if args.platform == 'bluesky':
            platform = BlueskyPlatform(page_size=args.page_size, max_pages=args.max_pages,
                                       thread_depth=args.thread_depth)
        elif args.platform == 'x':
            platform = XPlatform(settings.x_bearer_token, page_size=args.page_size,
                                 max_pages=args.max_pages)
        else:
            platform = RedditPlatform(settings.reddit_client_id, settings.reddit_client_secret,
                                      settings.reddit_user_agent, page_size=args.page_size,
                                      max_pages=args.max_pages, comment_limit=args.comment_limit)
    except ValueError as exc:
        parser.error(str(exc))
    try:
        try:
            worker = PollingWorker(platform, args.seed, poll_interval=args.interval,
                                   max_users=args.max_users, max_depth=args.max_depth)
        except ValueError as exc:
            parser.error(str(exc))
        if args.once:
            worker.poll_once()
        else:
            worker.run()
    except KeyboardInterrupt:
        pass
    finally:
        platform.close()


if __name__ == '__main__':
    main()
