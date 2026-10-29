import json
import httpx
import pytest
from pydantic import ValidationError
from app.config import Settings
from app.demo_data import generate_demo_data
from app.llm_classifier import ChatCompletionsProvider, Classifier, GeminiProvider
from app.schemas import Classification
from app.workflow import analyze_store, contexts


def test_dataset_reproducible_and_complete():
    first = generate_demo_data()
    assert first == generate_demo_data()
    assert 15 <= len(first.users) <= 25
    assert 30 <= len(first.contents) <= 50
    assert len(first.expected) == len(first.contents) + len(first.interactions)
    assert all(u.is_synthetic for u in first.users)


def test_mock_explicit_and_expected_labels_match(store, classifier):
    counts = analyze_store(store, classifier)
    assert counts['provider'] == 'mock'
    assert 'is_mock' not in counts
    assert counts['errors'] == 0
    assert counts['uncertain'] == sum(label.confidence < 0.65 or 'UNCERTAIN' in label.category or label.intent == 'UNCERTAIN'
                                      for label in generate_demo_data().expected.values())
    expected = generate_demo_data().expected
    for key, id_key in [('contents', 'content_id'), ('interactions', 'interaction_id')]:
        for node in store.data[key]:
            assert node['category'] == expected[node[id_key]].category
            assert node['intent'] == expected[node[id_key]].intent
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


@pytest.mark.parametrize('update', [{'severity': 4}, {'severity': True}, {'severity': 1.5}, {'confidence': 1.1}, {'confidence': float('nan')}, {'category': []}, {'category': ['HATE','BENIGN']}, {'intent': 'SUPPORT'}, {'intent': 'PARTICIPATION'}, {'intent': 'AMPLIFICATION'}, {'intent': 'COUNTER_SPEECH'}, {'intent': 'REPORTING'}, {'category': ['COUNTER_SPEECH']}, {'extra': 'invalid'}])
def test_validation(update):
    payload = generate_demo_data().expected['c001'].model_dump()
    with pytest.raises(ValidationError):
        Classification.model_validate({**payload, **update})


class FailingProvider:
    def __init__(self, error):
        self.error = error

    def classify(self, context):
        raise self.error


@pytest.mark.parametrize('error', [ValueError('invalid JSON'), httpx.ConnectError('offline'), KeyError('choices')])
def test_failures_are_uncertain_retryable_and_not_mock_fallback(store, error):
    classifier = Classifier(Settings(_env_file=None, llm_provider='chat_completions', llm_model='test-model'), FailingProvider(error))
    first = analyze_store(store, classifier)
    second = analyze_store(store, classifier)
    assert first['errors'] == first['analyzed'] == second['analyzed']
    assert first['provider'] == 'chat_completions'
    assert all(n['severity'] == 0 and n['status'] == 'error'
               and n['intent'] == 'UNCERTAIN' and n['category'] == ['UNCERTAIN']
               for n in store.data['contents'])


def test_real_provider_requires_configuration():
    with pytest.raises(ValueError, match='requires'):
        Classifier(Settings(_env_file=None, llm_provider='chat_completions', llm_api_key='', llm_model=''))


def test_chat_completions_request_and_validation(monkeypatch):
    expected = generate_demo_data().expected['c001']
    captured = []
    def handler(request):
        captured.append(request)
        return httpx.Response(200, json={'choices': [{'message': {'content': expected.model_dump_json()}}]})
    actual_client = httpx.Client
    monkeypatch.setattr('app.llm_classifier.httpx.Client', lambda **kwargs: actual_client(transport=httpx.MockTransport(handler), **kwargs))
    config = Settings(_env_file=None, llm_provider='chat_completions', llm_api_key='test-only-key',
                      llm_model='configured-model', llm_base_url='https://provider.example/v1')
    classification, raw = ChatCompletionsProvider(config).classify({'text': 'synthetic text'})
    assert classification == expected
    body = json.loads(captured[0].content)
    assert body['model'] == 'configured-model'
    assert body['response_format'] == {'type': 'json_object'}
    assert 'expected' not in body['messages'][1]['content']
    assert raw


def test_gemini_request_and_validation(monkeypatch):
    expected = generate_demo_data().expected['c001']
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json={'candidates': [{'content': {
            'parts': [{'text': expected.model_dump_json()}]}}]})

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


def test_uncertain_intent_with_known_category_is_valid_and_counted(store, classifier):
    class UnclearStanceProvider:
        def classify(self, context):
            result = Classification(category=['HATE'], severity=0, confidence=0.9,
                                    intent='UNCERTAIN', explanation='Topic known; stance unclear.')
            return result, result.model_dump_json()

    classifier.provider = UnclearStanceProvider()
    counts = analyze_store(store, classifier)
    assert counts['errors'] == 0
    assert counts['uncertain'] == counts['analyzed']


def test_demo_reaction_labels_follow_explicit_synthetic_target_stances():
    data = generate_demo_data()
    like = next(i for i in data.interactions if i.user_id == 'u04')
    dislike = next(i for i in data.interactions if i.user_id == 'u18')
    assert data.expected[like.interaction_id].category == ['HATE']
    assert data.expected[like.interaction_id].intent == 'ENDORSEMENT'
    assert data.expected[like.interaction_id].severity == 1
    assert data.expected[dislike.interaction_id].category == ['HATE']
    assert data.expected[dislike.interaction_id].intent == 'CONDEMNATION'
    assert data.expected[dislike.interaction_id].severity == 1
    for reaction in (i for i in data.interactions if i.user_id == 'u20'):
        expected = data.expected[reaction.interaction_id]
        assert expected.category == data.expected[reaction.target_id].category
        assert expected.intent == data.expected[reaction.target_id].intent

    opposite = {'ENDORSEMENT': 'CONDEMNATION', 'CONDEMNATION': 'ENDORSEMENT',
                'NEUTRAL': 'NEUTRAL', 'UNCERTAIN': 'UNCERTAIN'}
    for reaction in data.interactions:
        if reaction.interaction_type not in {'like', 'dislike'}:
            continue
        target = data.expected[reaction.target_id]
        result = data.expected[reaction.interaction_id]
        assert result.category == target.category
        assert result.severity == 1
        assert result.intent == (target.intent if reaction.interaction_type == 'like'
                                 else opposite[target.intent])
