from threading import Lock
from unittest.mock import Mock

import pytest

from app.schemas import ActivityBatch, Content, User
from app.testing import DatabaseEvents, run_pipeline
from app.workflow import analyze_store, score_store


def test_database_events_ingest_and_delete(store, caplog):
    caplog.set_level('INFO', logger='app.testing')
    emit = DatabaseEvents(store, Lock())
    actor = User(user_id='did:plc:live', display_name='live', is_synthetic=False)
    emit({'type': 'pool_member', 'user': actor.model_dump()})
    content = Content(content_id='at://live/post', user_id=actor.user_id,
                      content_type='post', text='New', created_at='now')
    emit({'type': 'Content', 'record': content.model_dump()})
    assert f'Saved post to DB: {content.content_id}' in caplog.text
    emit({'type': 'Interaction', 'record': dict(interaction_id='live-like', user_id=actor.user_id,
          target_id=content.content_id, interaction_type='like', created_at='now')})
    assert any(c['content_id'] == content.content_id for c in store.snapshot()['contents'])
    emit({'type': 'delete', 'record_type': 'Content', 'record_id': content.content_id})
    snapshot = store.snapshot()
    assert not any(c['content_id'] == content.content_id for c in snapshot['contents'])
    assert not any(i['interaction_id'] == 'live-like' for i in snapshot['interactions'])
    with pytest.raises(ValueError):
        emit({'type': 'unsupported'})


def test_scores_persist_after_analysis_and_fingerprints_skip_repeated_calls(store, classifier):
    first = analyze_store(store, classifier, lock=Lock())
    classifier.provider.classify = Mock(wraps=classifier.provider.classify)
    assert score_store(store, classifier, lock=Lock()) == 20
    harmful = next(u for u in store.snapshot()['users'] if u['user_id'] == 'u01')
    assert harmful['category_scores']['HATE'] == 100
    assert harmful['analysis_complete'] is True
    second = analyze_store(store, classifier, lock=Lock())
    assert second['cached'] == first['analyzed']
    assert second['analyzed'] == 0
    classifier.provider.classify.assert_not_called()
    assert score_store(store, classifier) == 20


def test_stream_edit_during_model_call_discards_outdated_analysis(store, classifier):
    original = classifier.provider.classify
    lock = Lock()

    def change_during_call(context):
        # The model call must not hold the ingestion lock.
        assert lock.acquire(blocking=False)
        try:
            store.data['contents'][0]['text'] = 'Updated during inference'
        finally:
            lock.release()
        return original(context)

    classifier.provider.classify = change_during_call
    counts = analyze_store(store, classifier, lock=lock)
    assert counts['outdated'] >= 1
    assert 'fingerprint' not in store.data['contents'][0]


def test_new_edits_and_parent_context_changes_do_not_score_stale_results(store, classifier):
    analyze_store(store, classifier)
    # u02's threatening comment uses c005 as parent context.
    parent = next(c for c in store.data['contents'] if c['content_id'] == 'c005')
    parent['text'] = 'Changed parent after classification'
    score_store(store, classifier, lock=Lock())
    user = next(u for u in store.snapshot()['users'] if u['user_id'] == 'u02')
    assert user['overall_score'] == 0
    assert user['analysis_complete'] is False
    refreshed = analyze_store(store, classifier, lock=Lock())
    assert refreshed['analyzed'] > 0
    assert refreshed['cached'] > 0
    score_store(store, classifier, lock=Lock())
    user = next(u for u in store.snapshot()['users'] if u['user_id'] == 'u02')
    assert user['analysis_complete'] is True


def test_deletion_during_model_call_does_not_write_deleted_record(store, classifier):
    original = classifier.provider.classify

    def delete_during_call(context):
        store.delete_record('Content', 'c001')
        return original(context)

    classifier.provider.classify = delete_during_call
    counts = analyze_store(store, classifier, lock=Lock())
    assert counts['outdated'] >= 1
    assert not any(c['content_id'] == 'c001' for c in store.snapshot()['contents'])


class FakePlatform:
    def resolve_user(self, handle):
        return User(user_id=handle, display_name=handle, is_synthetic=False)

    def poll_user(self, user):
        from app.platforms.base import PollResult
        return PollResult(activity=ActivityBatch(users=[user], contents=[Content(
            content_id='at://initial/post', user_id=user.user_id, content_type='post',
            text='Initial post', created_at='now')], interactions=[]))


def test_once_pipeline_ingests_analyzes_and_saves_scores(store, classifier):
    store.data = {'users': [], 'contents': [], 'interactions': []}
    run_pipeline(FakePlatform(), store, classifier, ['did:plc:initial'], once=True)
    snapshot = store.snapshot()
    assert snapshot['contents'][0]['status'] == 'complete'
    assert snapshot['users'][0]['overall_score'] == 0
    assert snapshot['users'][0]['analysis_complete'] is True


def test_stream_ingestion_and_periodic_cycles_reuse_cache(store, classifier, monkeypatch):
    import app.testing as pipeline
    store.data = {'users': [], 'contents': [], 'interactions': []}
    thread = Mock()
    thread_factory = Mock(return_value=thread)
    monkeypatch.setattr(pipeline, 'Thread', thread_factory)

    def stream(self):
        assert store.snapshot()['contents'][0]['content_id'] == 'at://initial/post'
        self.emit({'type': 'Content', 'record': Content(
            content_id='at://live/post', user_id='did:plc:initial', content_type='post',
            text='Live post', created_at='now').model_dump()})

    monkeypatch.setattr(pipeline.StreamingWorker, 'run', stream)
    run_pipeline(FakePlatform(), store, classifier, ['did:plc:initial'], analysis_interval=10)
    thread.start.assert_called_once()
    assert thread_factory.call_args.kwargs['daemon'] is True
    cycle_loop = thread_factory.call_args.kwargs['target']
    calls = []
    original = pipeline.analyze_store

    def analyze(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(result)
        return result

    class EndTest(Exception):
        pass

    def sleep(interval):
        assert interval == 10
        if len(calls) == 2:
            raise EndTest()

    monkeypatch.setattr(pipeline, 'analyze_store', analyze)
    monkeypatch.setattr(pipeline, 'sleep', sleep)
    with pytest.raises(EndTest):
        cycle_loop()
    assert calls[0]['analyzed'] == 2
    assert calls[1]['cached'] == 2
    assert calls[1]['analyzed'] == 0
    assert 'overall_score' in store.snapshot()['users'][0]


def test_stream_errors_propagate_directly(store, classifier, monkeypatch):
    import app.testing as pipeline
    monkeypatch.setattr(pipeline, 'Thread', Mock())

    def fail(self):
        raise RuntimeError('Database write failed')

    monkeypatch.setattr(pipeline.StreamingWorker, 'run', fail)
    with pytest.raises(RuntimeError, match='Database write failed'):
        run_pipeline(FakePlatform(), store, classifier, ['did:plc:initial'])
