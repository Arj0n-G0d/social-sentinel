"""Context construction is shared by live and deterministic test workflows."""
import time
from collections.abc import Sequence
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


def analyze_store(store, classifier):
    data = store.snapshot() # Reads the current users, content, and interactions from the database.
    nodes = {('Content', c['content_id']): c for c in data['contents']}
    nodes.update({('Interaction', i['interaction_id']): i for i in data['interactions']})
    """
        {
                ('Content', 'c001'): {...},
                ('Interaction', 'i001'): {...}
        }
    """
    counts = {'analyzed': 0, 'cached': 0, 'errors': 0, 'uncertain': 0,
              'provider': classifier.provider_name, 'model': classifier.model}
    for label, ident, context in contexts(data['contents'], data['interactions']):
        node = nodes[label, ident]
        if node.get('fingerprint') == classifier.fingerprint(context) and node.get('status') == 'complete':
            counts['cached'] += 1
            continue
        print(f'======= Analyzing {ident} =======')
        result = classifier.analyze(context)
        print(result)
        store.save_analysis(label, ident, result)
        counts['analyzed'] += 1
        counts['errors'] += result.status == 'error'
        counts['uncertain'] += ('UNCERTAIN' in result.classification.category
                                or result.classification.intent == 'UNCERTAIN'
                                or result.classification.confidence < 0.65)
        time.sleep(5)
#     node = nodes["Content", "c007"]
#     context = next(
#         context
#         for label, ident, context in contexts(data['contents'], data['interactions'])
#         if label == 'Content' and ident == node['content_id']
#     )
#     result = classifier.analyze(context)
#     print(result)
#     store.save_analysis("Content", "c007", result)
    return counts


