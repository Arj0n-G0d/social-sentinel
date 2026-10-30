"""Transparent, conservative demonstration scoring; no graph-degree features."""
from app.schemas import HARMFUL_CATEGORIES as CATEGORIES, CATEGORY_THRESHOLD


def band(score):
    return 'Critical' if score >= 75 else 'High' if score >= 50 else 'Moderate' if score >= 25 else 'Low'


def score_user(user_id: str, contents: list[dict], interactions: list[dict]) -> dict:
    evidence = []
    totals = dict.fromkeys(CATEGORIES, 0.0)
    # Repeated copies of the same record ID count once; separate actions all count.
    items = {('Content', c['content_id']): (c, 'content')
             for c in contents if c['user_id'] == user_id}
    items.update({('Interaction', i['interaction_id']): (i, i['interaction_type'])
                  for i in interactions if i['user_id'] == user_id})
    for item, kind in items.values():
        ident = item.get('content_id', item.get('interaction_id'))
        probabilities = item.get('probabilities', {})
        categories = [c for c in CATEGORIES if probabilities.get(c, 0) >= CATEGORY_THRESHOLD]
        classification = categories or ['BENIGN']
        reason = None
        if item.get('status') != 'complete':
            reason = 'Unprocessed or failed analysis.'
        elif kind in {'like', 'dislike'}:
            reason = f'A {kind} alone is not evidence of harmful behavior.'
        elif not categories:
            reason = 'No category probability meets the 0.5 threshold.'
        weight = 0.75 if kind == 'share' else 1.0
        category_points = {} if reason else {c: 36 * probabilities[c] * weight for c in categories}
        points = max(category_points.values(), default=0)
        evidence.append({'evidence_id': ident, 'kind': kind,
                         'text': item.get('text', ''), 'category': classification,
                         'probabilities': probabilities,
                         'candidate_category_points': {c: round(p, 3) for c, p in category_points.items()},
                         'explanation': item.get('explanation'),
                         'provider': item.get('provider'), 'model': item.get('model'),
                         'status': item.get('status', 'unprocessed'),
                         'candidate_points': round(points, 3),
                         'contributions': {c: round(p, 3) for c, p in category_points.items()},
                         'reason': reason or 'Eligible authored harm or context-supported harmful share.'})
        for category, points in category_points.items():
            totals[category] += points
    scores = {category: round(min(100, points), 2) for category, points in totals.items()}
    overall = max(scores.values(), default=0)
    return {'overall_score': overall, 'review_level': band(overall),
            'flag_for_review': overall >= 25, 'category_scores': scores,
            'category_levels': {c: band(s) for c, s in scores.items()},
            'evidence': evidence,
            'analysis_complete': all(e['status'] == 'complete' for e in evidence),
            'contains_mock_analysis': any(e['provider'] == 'mock' for e in evidence),
            'analysis_sources': [dict(provider=p, model=m) for p, m in sorted({
                (e['provider'], e['model']) for e in evidence if e['provider']})],
            'explanation': ('Category totals sum all eligible items, capped at 100; '
                            'overall is the highest category score. '
                            f'{sum(bool(e["contributions"]) for e in evidence)} evidence items contribute. '
                            'A flag requests human review, not a conclusive judgment.'),
            'threshold_notice': 'Demonstration thresholds; not validated moderation standards.'}
