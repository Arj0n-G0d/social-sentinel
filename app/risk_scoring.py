"""Transparent, conservative demonstration scoring; no graph-degree features."""
CATEGORIES = ('HATE', 'HARASSMENT', 'VIOLENCE', 'SPAM')
HARMFUL_INTENTS = {'ENDORSEMENT'}
MIN_CONFIDENCE = 0.65


def band(score):
    return 'Critical' if score >= 75 else 'High' if score >= 50 else 'Moderate' if score >= 25 else 'Low'


def score_user(user_id: str, contents: list[dict], interactions: list[dict]) -> dict:
    by_id = {c['content_id']: c for c in contents}
    evidence = []
    # One contribution per incident/category: take the strongest, not sum duplicates.
    incidents = {category: {} for category in CATEGORIES}

    def root_id(cid):
        seen = set()
        while cid in by_id and by_id[cid].get('parent_id') and cid not in seen:
            seen.add(cid)
            cid = by_id[cid]['parent_id']
        return cid

    items = [(c, 'content') for c in contents if c['user_id'] == user_id]
    items += [(i, i['interaction_type']) for i in interactions if i['user_id'] == user_id]
    for item, kind in items:
        ident = item.get('content_id', item.get('interaction_id'))
        classification = item.get('category', [])
        categories = [c for c in classification if c in CATEGORIES]
        confidence = item.get('confidence', 0)
        reason = None
        if item.get('status') != 'complete':
            reason = 'Unprocessed or failed analysis.'
        elif kind in {'like', 'dislike'}:
            reason = f'A {kind} alone is not evidence of harmful behavior.'
        elif 'UNCERTAIN' in classification or item.get('intent') not in HARMFUL_INTENTS:
            reason = 'Intent is uncertain, protective, or neutral.'
        elif confidence < MIN_CONFIDENCE:
            reason = 'Confidence below 0.65; excluded from scoring.'
        elif not categories or not item.get('severity', 0):
            reason = 'No harmful category with positive severity.'
        incident = root_id(item['target_id'] if kind != 'content' else item['content_id'])
        weight = 0.75 if kind == 'share' else 1.0
        points = 0 if reason else 12 * item['severity'] * confidence * weight
        evidence.append({'evidence_id': ident, 'kind': kind, 'incident_id': incident,
                         'text': item.get('text', ''), 'category': classification,
                         'severity': item.get('severity'), 'confidence': confidence,
                         'intent': item.get('intent'), 'explanation': item.get('explanation'),
                         'provider': item.get('provider'), 'model': item.get('model'),
                         'status': item.get('status', 'unprocessed'),
                         'candidate_points': round(points, 3), 'contributions': {},
                         'reason': reason or 'Eligible authored harm or context-supported harmful share.'})
        if points:
            for category in categories:
                old = incidents[category].get(incident)
                if old is None or old[0] < points:
                    incidents[category][incident] = (points, len(evidence) - 1)
    scores, incident_counts = {}, {}
    for category, grouped in incidents.items():
        for points, index in grouped.values():
            evidence[index]['contributions'][category] = round(points, 3)
        scores[category] = round(min(100, sum(p for p, _ in grouped.values())), 2)
        incident_counts[category] = len(grouped)
    overall = max(scores.values(), default=0)
    return {'overall_score': overall, 'review_level': band(overall),
            'flag_for_review': overall >= 25, 'category_scores': scores,
            'category_levels': {c: band(s) for c, s in scores.items()},
            'distinct_incidents': incident_counts, 'evidence': evidence,
            'analysis_complete': all(e['status'] == 'complete' for e in evidence),
            'contains_mock_analysis': any(e['provider'] == 'mock' for e in evidence),
            'analysis_sources': [dict(provider=p, model=m) for p, m in sorted({
                (e['provider'], e['model']) for e in evidence if e['provider']})],
            'explanation': ('Category totals sum the strongest evidence per incident; '
                            'overall is the highest category score. '
                            f'{sum(bool(e["contributions"]) for e in evidence)} evidence items contribute. '
                            'A flag requests human review, not a conclusive judgment.'),
            'threshold_notice': 'Demonstration thresholds; not validated moderation standards.'}
