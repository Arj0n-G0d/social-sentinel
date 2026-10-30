from fastapi.testclient import TestClient
from app.main import create_app


def test_end_to_end_api_with_test_double(store, classifier):
    with TestClient(create_app(store=store, classifier=classifier)) as client:
        assert client.post('/ingest-demo').json()['users'] == 20
        assert client.post('/analyze').json()['errors'] == 0
        assert client.post('/analyze').json()['analyzed'] == 0
        users = client.get('/users').json()
        assert len(users) == 20
        assert all('evidence' not in u for u in users)
        harmful = client.get('/users/u01').json()
        assert harmful['category_scores']['HATE'] == 100
        assert harmful['evidence'][0]['provider'] == 'mock'
        assert set(harmful['evidence'][0]['probabilities']) == {'HATE', 'HARASSMENT', 'VIOLENCE', 'SPAM'}
        assert 'severity' not in harmful['evidence'][0]
        assert 'confidence' not in harmful['evidence'][0]
        assert 'is_mock' not in harmful['evidence'][0]
        assert harmful['contains_mock_analysis'] is True
        assert client.get('/users/u03').json()['overall_score'] == 0
        dislike_user = client.get('/users/u18').json()
        dislike = next(e for e in dislike_user['evidence'] if e['kind'] == 'dislike')
        assert 'intent' not in dislike
        assert dislike['candidate_points'] == 0
        assert dislike['contributions'] == {}
        assert client.get('/graph/u03').status_code == 200
        assert client.get('/users/missing').status_code == 404
        assert client.get('/graph/missing').status_code == 404
        assert client.get('/docs').status_code == 200
