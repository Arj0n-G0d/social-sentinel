import argparse

from app.demo_data import generate_demo_data
from app.graph_store import GraphStore
from app.llm_classifier import Classifier, GeminiProvider
from app.config import Settings
from app.workflow import analyze_store
from app.platforms.reddit import RedditPlatform
from app.platforms.x import XPlatform
from app.platforms.bluesky import BlueskyPlatform


def setup_demo():
    settings = Settings()
    seedDataset = generate_demo_data()
    graphStore = GraphStore(settings)
    geminiProvider = GeminiProvider(settings)
    classifier = Classifier(settings, geminiProvider)

    # Seeding the DB
    # graphStore.initialize()
    # graphStore.ingest(seedDataset)

    # Analyzing the content
    # analyze_store(graphStore, classifier)
    return seedDataset, graphStore, classifier


def main():
    platform = BlueskyPlatform()

    try:
        user = platform.resolve_user("arjungaur.bsky.social")
        result = platform.poll_user(user)
        print(result.model_dump_json(indent=2))

        # Ingest
        settings = Settings()
        graphStore = GraphStore(settings)
        graphStore.initialize()
        graphStore.ingest(result.activity)

        # Analyze and store
        geminiProvider = GeminiProvider(settings)
        classifier = Classifier(settings, geminiProvider)
        analyze_store(graphStore, classifier)

    finally:
        platform.close()


if __name__ == '__main__':
    main()
