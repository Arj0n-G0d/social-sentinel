from fastapi import APIRouter, HTTPException, Request
from app.demo_data import generate_demo_data
from app.risk_scoring import score_user
from app.workflow import analyze_store

router = APIRouter()


def profile(user, data):
    return {**user, **score_user(user['user_id'], data['contents'], data['interactions'])}


@router.post('/ingest-demo')
def ingest_demo(request: Request):
    with request.app.state.mutation_lock:
        return {'dataset': 'deterministic-synthetic-v1',
                **request.app.state.store.ingest(generate_demo_data())}


@router.post('/analyze')
def analyze(request: Request):
    with request.app.state.mutation_lock:
        result = analyze_store(request.app.state.store, request.app.state.classifier)
    return result


@router.get('/users')
def users(request: Request):
    data = request.app.state.store.snapshot()
    return [{k: v for k, v in profile(user, data).items() if k != 'evidence'}
            for user in sorted(data['users'], key=lambda u: u['user_id'])]


@router.get('/users/{user_id}')
def user_details(user_id: str, request: Request):
    data = request.app.state.store.snapshot()
    user = next((u for u in data['users'] if u['user_id'] == user_id), None)
    if user is None:
        raise HTTPException(404, 'User not found')
    return profile(user, data)


@router.get('/graph/{user_id}')
def user_graph(user_id: str, request: Request):
    result = request.app.state.store.neighborhood(user_id)
    if result is None:
        raise HTTPException(404, 'User not found')
    return result
