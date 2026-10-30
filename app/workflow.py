"""Context construction is shared by live and deterministic test workflows."""
from collections.abc import Sequence
from contextlib import nullcontext
from app.risk_scoring import score_user
from app.schemas import Content, Interaction

def contexts(contents: Sequence[Content | dict], interactions: Sequence[Interaction | dict]):
    # Workers supply canonical models; Neo4j snapshots supply property mappings.
    # Both paths use exactly the same context construction and field names.
    contents = [c.model_dump() if isinstance(c, Content) else c for c in contents]
    interactions = [i.model_dump() if isinstance(i, Interaction) else i for i in interactions]
    by_id = {c['content_id']: c for c in contents}
    for c in contents:
        """
                Produces
                (
                        'Content',
                        'c002',
                        {
                                'kind': 'comment',
                                'text': 'This is wrong. Everyone deserves respect.',
                                'parent_text': 'The harmful post being challenged.'
                        }       
                )
                for each content (post / comment)
        """
        parent = by_id.get(c.get('parent_id'))
        yield 'Content', c['content_id'], {
            'kind': c['content_type'], 'text': c['text'],
            'parent_text': parent['text'] if parent else None}
    for i in interactions:
        target = by_id.get(i['target_id']) # Find the post or comment the person reacted to or shared.
        target_parent = by_id.get(target.get('parent_id')) if target else None # If the target is a comment, also retrieve what that comment replies to.
        """
                For an interaction, we send:
                        1. The interaction itself — like, dislike, or share, plus any accompanying text.
                        2. Its target — the post or comment it reacts to.
                        3. The target’s immediate parent, if the target is a comment.
                For examples like
                        Post A
                        └── Comment B
                                └── Comment C
                                        └── Like
                        We send Comment C and Comment B as context. We don’t include Post A.
        """
        yield 'Interaction', i['interaction_id'], {
            'kind': i['interaction_type'], 'text': i.get('text', ''),
            'target_text': target['text'] if target else None,
            'target_parent_text': target_parent['text'] if target_parent else None}


def analyze_store(store, classifier, *, lock=None):
    """Skip cached inputs; never hold the ingestion lock during a model call."""
    guard = lock if lock is not None else nullcontext()
    with guard:
        data = store.snapshot()
    nodes = {('Content', c['content_id']): c for c in data['contents']}
    nodes.update({('Interaction', i['interaction_id']): i for i in data['interactions']})
    counts = {'analyzed': 0, 'cached': 0, 'errors': 0, 'benign': 0, 'outdated': 0,
              'provider': classifier.provider_name, 'model': classifier.model}
    for label, ident, context in contexts(data['contents'], data['interactions']):
        node = nodes[label, ident]
        fingerprint = classifier.fingerprint(context)
        if node.get('fingerprint') == fingerprint and node.get('status') == 'complete':
            counts['cached'] += 1
            continue
        result = classifier.analyze(context)
        with guard:
            if lock is not None:
                # Stream edits/deletions may have changed this item or its context.
                current = store.snapshot()
                current_context = next((ctx for kind, key, ctx in contexts(
                    current['contents'], current['interactions'])
                    if kind == label and key == ident), None)
                if current_context is None or classifier.fingerprint(current_context) != fingerprint:
                    counts['outdated'] += 1
                    continue
            store.save_analysis(label, ident, result)
        counts['analyzed'] += 1
        counts['errors'] += result.status == 'error'
        counts['benign'] += result.status == 'complete' and result.classification.category == ['BENIGN']
    return counts


def score_store(store, classifier, *, lock=None):
    """Score each user's own records and persist summaries on User nodes."""
    guard = lock if lock is not None else nullcontext()
    with guard:
        data = store.snapshot()
        nodes = {('Content', c['content_id']): c for c in data['contents']}
        nodes.update({('Interaction', i['interaction_id']): i for i in data['interactions']})
        for label, ident, context in contexts(data['contents'], data['interactions']):
            node = nodes[label, ident]
            if node.get('fingerprint') != classifier.fingerprint(context):
                # A stream edit since analysis must not score using old probabilities.
                node['status'] = 'unprocessed'
        contents, interactions = {}, {}
        for content in data['contents']:
            contents.setdefault(content['user_id'], []).append(content)
        for interaction in data['interactions']:
            interactions.setdefault(interaction['user_id'], []).append(interaction)
        for user in data['users']:
            user_id = user['user_id']
            result = score_user(user_id, contents.get(user_id, []), interactions.get(user_id, []))
            store.save_user_scores(user_id, result)
        return len(data['users'])
