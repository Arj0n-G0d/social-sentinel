import json
import httpx
import pytest
from pydantic import ValidationError
from app.config import Settings
from app.demo_data import generate_demo_data, generate_demo_labels
from app.llm_classifier import Classifier, GeminiProvider
from app.schemas import ActivityBatch, Classification
from app.workflow import analyze_store, contexts


def test_activity_batch_reproducible_and_complete():
    first = generate_demo_data()
    assert first == generate_demo_data()
    assert 15 <= len(first.users) <= 25
    assert 30 <= len(first.contents) <= 50
    assert type(first) is ActivityBatch
    assert set(first.model_dump()) == {'users', 'contents', 'interactions'}
    assert set(generate_demo_labels()) == {c.content_id for c in first.contents} | {i.interaction_id for i in first.interactions}
    assert len(generate_demo_labels()) == len(first.contents) + len(first.interactions)
    assert all(u.is_synthetic for u in first.users)


def test_mock_explicit_and_expected_labels_match(store, classifier):
    counts = analyze_store(store, classifier)
    assert counts['provider'] == 'mock'
    assert 'is_mock' not in counts
    assert counts['errors'] == 0
    assert counts['benign'] == sum(label.category == ['BENIGN']
                                      for label in generate_demo_labels().values())
    expected = generate_demo_labels()
    for key, id_key in [('contents', 'content_id'), ('interactions', 'interaction_id')]:
        for node in store.data[key]:
            assert node['category'] == expected[node[id_key]].category
            assert 'intent' not in node
            assert node['provider'] == 'mock' and 'MOCK' in node['explanation']
            assert 'is_mock' not in node


def test_analysis_cached_and_reingest_preserves_cache(store, classifier):
    first = analyze_store(store, classifier)
    store.ingest(generate_demo_data())
    second = analyze_store(store, classifier)
    assert second['analyzed'] == 0
    assert second['cached'] == first['analyzed']


def test_context_change_invalidates_parent_and_interactions(store, classifier):
    analyze_store(store, classifier)
    store.data['contents'][0]['text'] = 'Changed parent content'
    counts = analyze_store(store, classifier)
    # Changed parent, comments on it, and every interaction targeting it.
    assert counts['analyzed'] > 5
    assert counts['cached'] > 0


def test_version_model_and_endpoint_invalidate_cache(store, classifier):
    analyze_store(store, classifier)
    for attribute in ('analysis_version', 'llm_base_url'):
        setattr(classifier.settings, attribute, 'changed')
        assert analyze_store(store, classifier)['cached'] == 0
    classifier.model = 'another-model'
    assert analyze_store(store, classifier)['cached'] == 0


def test_comments_and_share_have_context(store):
    rows = list(contexts(store.data['contents'], store.data['interactions']))
    comment = next(ctx for _, _, ctx in rows if ctx['kind'] == 'comment')
    share = next(ctx for _, _, ctx in rows if ctx['kind'] == 'share')
    assert comment['parent_text']
    assert share['target_text']
    dislike = next(ctx for _, _, ctx in rows if ctx['kind'] == 'dislike')
    assert dislike['target_text']
    assert all('expected' not in ctx for _, _, ctx in rows)


@pytest.mark.parametrize('probabilities', [
    {}, {'HATE': 0.2},
    dict(HATE=1.1, HARASSMENT=0.0, VIOLENCE=0.0, SPAM=0.0),
    dict(HATE=float('nan'), HARASSMENT=0.0, VIOLENCE=0.0, SPAM=0.0),
    dict(HATE=-0.1, HARASSMENT=0.0, VIOLENCE=0.0, SPAM=0.0),
    dict(HATE=True, HARASSMENT=0.0, VIOLENCE=0.0, SPAM=0.0),
    dict(HATE='0.5', HARASSMENT=0.0, VIOLENCE=0.0, SPAM=0.0),
    dict(HATE=0.2, HARASSMENT=0.0, VIOLENCE=0.0, SPAM=0.0, BENIGN=0.8),
])
def test_invalid_probabilities_are_rejected(probabilities):
    with pytest.raises(ValidationError):
        Classification(probabilities=probabilities)


@pytest.mark.parametrize('extra', [{'severity': 2}, {'confidence': 0.9}, {'intent': 'NEUTRAL'},
                                   {'category': ['HATE']}])
def test_model_returns_probabilities_only(extra):
    with pytest.raises(ValidationError):
        Classification(probabilities=dict.fromkeys(['HATE', 'HARASSMENT', 'VIOLENCE', 'SPAM'], 0.1), **extra)


class FailingProvider:
    def __init__(self, error):
        self.error = error

    def classify(self, context):
        raise self.error


@pytest.mark.parametrize('error', [ValueError('invalid JSON'), httpx.ConnectError('offline'), KeyError('choices')])
def test_failures_are_zero_confidence_retryable_and_not_mock_fallback(store, error):
    classifier = Classifier(Settings(_env_file=None, llm_provider='gemini', llm_model='test-model'), FailingProvider(error))
    first = analyze_store(store, classifier)
    second = analyze_store(store, classifier)
    assert first['errors'] == first['analyzed'] == second['analyzed']
    assert first['provider'] == 'gemini'
    assert all(all(p == 0 for p in n['probabilities'].values()) and n['status'] == 'error'
               and 'intent' not in n and n['category'] == ['BENIGN']
               for n in store.data['contents'])


def test_real_provider_requires_configuration():
    with pytest.raises(ValueError, match='requires'):
        Classifier(Settings(_env_file=None, llm_provider='gemini', llm_api_key='', llm_model=''))


def test_gemini_request_and_validation(monkeypatch):
    expected = generate_demo_labels()['c001']
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json={'candidates': [{'content': {
            'parts': [{'text': expected.model_dump_json(exclude={'category'})}]}}]})

    actual_client = httpx.Client
    monkeypatch.setattr('app.llm_classifier.httpx.Client', lambda **kwargs: actual_client(
        transport=httpx.MockTransport(handler), **kwargs))
    settings = Settings(_env_file=None, llm_provider='gemini', llm_api_key='test-key',
                        llm_model='gemini-flash-latest',
                        llm_base_url='https://generativelanguage.googleapis.com/v1beta')
    classifier = Classifier(settings)
    assert isinstance(classifier.provider, GeminiProvider)
    result = classifier.analyze({'text': 'synthetic text'})
    assert result.status == 'complete'
    assert result.classification == expected
    request = captured[0]
    assert str(request.url).endswith('/v1beta/models/gemini-flash-latest:generateContent')
    assert request.headers['X-goog-api-key'] == 'test-key'
    body = json.loads(request.content)
    assert json.loads(body['contents'][0]['parts'][0]['text']) == {'text': 'synthetic text'}
    assert body['systemInstruction']['parts'][0]['text']
    assert body['generationConfig']['responseMimeType'] == 'application/json'


@pytest.mark.parametrize('hate,spam,expected', [
    (0.49, 0.1, ['BENIGN']), (0.5, 0.1, ['HATE']),
    (0.9, 0.8, ['HATE', 'SPAM']), (0.0, 0.0, ['BENIGN']),
])
def test_categories_derived_independently(hate, spam, expected):
    result = Classification(probabilities=dict(HATE=hate, HARASSMENT=0.0, VIOLENCE=0.0, SPAM=spam))
    assert result.category == expected
    assert result.model_dump()['category'] == expected


def test_demo_reactions_below_threshold():
    data = generate_demo_data()
    for reaction in data.interactions:
        if reaction.interaction_type in {'like', 'dislike'}:
            result = generate_demo_labels()[reaction.interaction_id]
            assert result.category == ['BENIGN']
            assert set(result.probabilities.model_dump()) == {'HATE', 'HARASSMENT', 'VIOLENCE', 'SPAM'}


def test_unknown_mock_input_has_zero_probabilities():
    classifier = Classifier(Settings(_env_file=None, llm_provider='mock'))
    result = classifier.analyze({'text': 'not a fixture'})
    assert result.classification.category == ['BENIGN']
    assert all(p == 0 for p in result.classification.probabilities.model_dump().values())


def test_prompt_requests_four_probabilities():
    from app.llm_classifier import SYSTEM_PROMPT
    assert all(name in SYSTEM_PROMPT for name in ['HATE', 'HARASSMENT', 'VIOLENCE', 'SPAM'])
    assert 'independent probability' in SYSTEM_PROMPT
