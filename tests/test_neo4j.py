"""Opt-in integration test. Requires a dedicated empty Neo4j database.
Run: RUN_NEO4J_TESTS=1 python -m pytest tests/test_neo4j.py -v
The deterministic demo IDs are MERGEd; no deletion is performed.
"""
import os
import pytest
from app.config import Settings
from app.demo_data import generate_demo_data
from app.graph_store import GraphStore
from app.llm_classifier import Classifier
from app.workflow import analyze_store, score_store

pytestmark = pytest.mark.skipif(os.getenv('RUN_NEO4J_TESTS') != '1', reason='Requires running Neo4j; opt in with RUN_NEO4J_TESTS=1')


def test_real_graph_ingestion_analysis_and_neighborhood():
    store = GraphStore(Settings())
    try:
        store.initialize()
        store.ingest(generate_demo_data())
        before = store.snapshot()
        store.ingest(generate_demo_data())
        after = store.snapshot()
        assert {k: len(v) for k, v in before.items()} == {k: len(v) for k, v in after.items()}
        classifier = Classifier(Settings(llm_provider='mock'))
        assert analyze_store(store, classifier)['errors'] == 0
        assert analyze_store(store, classifier)['analyzed'] == 0
        analyzed = store.snapshot()
        assert all(set(c['probabilities']) == {'HATE', 'HARASSMENT', 'VIOLENCE', 'SPAM'}
                   for c in analyzed['contents'])
        assert all('severity' not in c and 'confidence' not in c for c in analyzed['contents'])
        assert score_store(store, classifier) == 20
        saved_user = next(u for u in store.snapshot()['users'] if u['user_id'] == 'u01')
        assert saved_user['overall_score'] == 100
        assert saved_user['category_scores']['HATE'] == 100
        assert 'scores_updated_at' in saved_user
        graph = store.neighborhood('u05')
        assert {n['label'] for n in graph['nodes']} == {'User', 'Content', 'Interaction'}
        assert {e['type'] for e in graph['edges']} >= {'PERFORMED', 'TARGETS'}
        comment_graph = store.neighborhood('u02')
        assert 'REPLIES_TO' in {e['type'] for e in comment_graph['edges']}
        assert store.neighborhood('missing') is None
    finally:
        store.close()
