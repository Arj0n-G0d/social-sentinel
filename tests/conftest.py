from copy import deepcopy
import pytest
from app.config import Settings
from app.demo_data import generate_demo_data
from app.llm_classifier import Classifier
from app.workflow import analyze_store


class MemoryStore:
    """Test double only; production always uses Neo4j."""
    def __init__(self):
        self.data = {'users': [], 'contents': [], 'interactions': []}

    def initialize(self):
        pass

    def close(self):
        pass

    def ingest(self, dataset):
        for key, id_key in [('users', 'user_id'), ('contents', 'content_id'), ('interactions', 'interaction_id')]:
            current = {r[id_key]: r for r in self.data[key]}
            for row in getattr(dataset, key):
                record = row.model_dump(exclude_none=True)
                current.setdefault(record[id_key], {}).update(record)
            self.data[key] = list(current.values())
        return {k: len(v) for k, v in self.data.items()}

    def snapshot(self):
        return deepcopy(self.data)

    def save_analysis(self, label, ident, result):
        key, id_key = ('contents', 'content_id') if label == 'Content' else ('interactions', 'interaction_id')
        node = next(r for r in self.data[key] if r[id_key] == ident)
        node.update(result.model_dump(exclude={'classification', 'error'}))
        node.update(result.classification.model_dump())

    def save_user_scores(self, user_id, scores):
        user = next(u for u in self.data['users'] if u['user_id'] == user_id)
        user.update(deepcopy({key: value for key, value in scores.items() if key != 'evidence'}))

    def delete_record(self, label, ident):
        key, id_key = ('contents', 'content_id') if label == 'Content' else ('interactions', 'interaction_id')
        self.data[key] = [r for r in self.data[key] if r[id_key] != ident]
        if label == 'Content':
            self.data['interactions'] = [i for i in self.data['interactions'] if i['target_id'] != ident]

    def neighborhood(self, user_id):
        if not any(u['user_id'] == user_id for u in self.data['users']):
            return None
        return {'nodes': [], 'edges': []}


@pytest.fixture
def store():
    store = MemoryStore()
    store.ingest(generate_demo_data())
    return store


@pytest.fixture
def classifier():
    return Classifier(Settings(_env_file=None, llm_provider='mock'))


@pytest.fixture
def analyzed(store, classifier):
    analyze_store(store, classifier)
    return store.snapshot()
