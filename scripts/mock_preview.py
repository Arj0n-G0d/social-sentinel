"""Offline fixture/scoring preview. Does not exercise Neo4j or a real LLM."""
import json
from app.config import Settings
from app.demo_data import generate_demo_data
from app.llm_classifier import Classifier
from app.risk_scoring import score_user
from app.workflow import contexts


def preview():
    dataset = generate_demo_data()
    contents = [c.model_dump() for c in dataset.contents]
    interactions = [i.model_dump() for i in dataset.interactions]
    nodes = {c['content_id']: c for c in contents}
    nodes.update({i['interaction_id']: i for i in interactions})
    classifier = Classifier(Settings(_env_file=None, llm_provider='mock'))
    for _, ident, context in contexts(contents, interactions):
        result = classifier.analyze(context)
        nodes[ident].update(result.model_dump(exclude={'classification'}))
        nodes[ident].update(result.classification.model_dump())
    output = {'notice': 'MOCK fixture replay; no LLM predictions. Offline scoring preview; Neo4j not exercised.',
              'counts': {'users': len(dataset.users), 'contents': len(contents), 'interactions': len(interactions)},
              'users': []}
    for uid in ('u01', 'u02', 'u03', 'u04', 'u05', 'u14', 'u20'):
        score = score_user(uid, contents, interactions)
        output['users'].append({'user_id': uid, **{k: score[k] for k in
                                ('overall_score', 'review_level', 'category_scores', 'flag_for_review')}})
    return output


if __name__ == '__main__':
    print(json.dumps(preview(), indent=2))
