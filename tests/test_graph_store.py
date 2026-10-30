from unittest.mock import Mock

from app.config import Settings
from app.graph_store import GraphStore
from app.llm_classifier import Classifier


def test_initialize_invalidates_legacy_analysis(monkeypatch):
    driver = Mock()
    monkeypatch.setattr('app.graph_store.GraphDatabase.driver', Mock(return_value=driver))
    store = GraphStore(Settings(_env_file=None))
    store.query = Mock()
    store.initialize()
    migration = store.query.call_args.args[0]
    assert 'n.severity IS NOT NULL' in migration
    assert 'n.confidence IS NOT NULL' in migration
    assert 'REMOVE n.intent, n.category' in migration
    assert 'n.fingerprint' in migration
    assert 'n.status' in migration
    assert 'n.raw_output' in migration


def test_save_analysis_writes_category_only_and_removes_legacy_intent(monkeypatch):
    monkeypatch.setattr('app.graph_store.GraphDatabase.driver', Mock())
    store = GraphStore(Settings(_env_file=None))
    store.query = Mock()
    result = Classifier(Settings(_env_file=None, llm_provider='mock')).analyze({'text': 'unknown'})
    store.save_analysis('Content', 'post', result)
    query = store.query.call_args.args[0]
    properties = store.query.call_args.kwargs['properties']
    assert 'REMOVE n.intent' in query
    assert 'intent' not in properties
    assert properties['category'] == ['BENIGN']
    assert 'probabilities' not in properties
    assert 'severity' not in properties
    assert 'confidence' not in properties
    assert properties['probability_HATE'] == 0
    decoded = store._properties(properties)
    assert decoded['probabilities'] == dict(HATE=0, HARASSMENT=0, VIOLENCE=0, SPAM=0)
    assert 'probability_HATE' not in decoded


def test_user_score_summary_round_trips_through_neo4j_properties(monkeypatch):
    from app.risk_scoring import score_user
    monkeypatch.setattr('app.graph_store.GraphDatabase.driver', Mock())
    store = GraphStore(Settings(_env_file=None))
    store.query = Mock()
    result = score_user('user', [dict(content_id='post', user_id='user', text='Post',
                        probabilities=dict(HATE=0.9, HARASSMENT=0, VIOLENCE=0, SPAM=0),
                        status='complete')], [])
    store.save_user_scores('user', result)
    params = store.query.call_args.kwargs
    assert params['user_id'] == 'user'
    properties = params['properties']
    assert all(not isinstance(value, dict) for value in properties.values())
    assert properties['overall_score'] == 32.4
    assert properties['category_score_HATE'] == 32.4
    restored = store._properties(properties)
    assert restored['category_scores'] == result['category_scores']
    assert restored['analysis_sources'] == result['analysis_sources']
    assert 'scores_updated_at' in restored
    assert 'evidence' not in restored
