from copy import deepcopy
import pytest
from app.risk_scoring import score_user, band


def score(data, user):
    return score_user(user, data['contents'], data['interactions'])


def test_repeated_hate(analyzed):
    result = score(analyzed, 'u01')
    assert result['category_scores']['HATE'] == 100
    assert result['distinct_incidents']['HATE'] == 4
    assert result['review_level'] == 'Critical'


def test_threatening_comment(analyzed):
    result = score(analyzed, 'u02')
    assert result['category_scores']['VIOLENCE'] == 34.2
    assert result['flag_for_review']
    assert result['evidence'][0]['kind'] == 'content'
    assert result['evidence'][0]['intent'] == 'ENDORSEMENT'


@pytest.mark.parametrize('user', ['u03', 'u04', 'u05', 'u08', 'u09', 'u11', 'u12', 'u13', 'u15', 'u16', 'u17', 'u20'])
def test_protective_uncertain_and_benign_users(analyzed, user):
    assert score(analyzed, user)['overall_score'] == 0


def test_warning_share_differs_from_promotion(analyzed):
    assert score(analyzed, 'u05')['overall_score'] == 0
    assert score(analyzed, 'u14')['category_scores']['HATE'] == 25.65


def test_many_connections_do_not_transfer_risk(analyzed):
    assert len(score(analyzed, 'u20')['evidence']) >= 18
    assert score(analyzed, 'u20')['overall_score'] == 0


def test_spam_repetition(analyzed):
    assert score(analyzed, 'u07')['category_scores']['SPAM'] == 100


@pytest.mark.parametrize('kind,user_id', [('like', 'u04'), ('dislike', 'u18')])
def test_reactions_cannot_contribute_even_if_classifier_claims_endorsement(analyzed, kind, user_id):
    node = next(i for i in analyzed['interactions'] if i['interaction_type'] == kind and i['user_id'] == user_id)
    node.update(category=['HATE'], severity=3, confidence=1, intent='ENDORSEMENT')
    result = score(analyzed, user_id)
    assert result['overall_score'] == 0
    evidence = next(e for e in result['evidence'] if e['kind'] == kind)
    assert evidence['contributions'] == {}
    assert evidence['reason'] == f'A {kind} alone is not evidence of harmful behavior.'


@pytest.mark.parametrize('field,value', [('confidence', .64), ('category', ['UNCERTAIN']), ('intent', 'NEUTRAL'), ('intent', 'UNCERTAIN'), ('intent', 'CONDEMNATION'), ('status', 'error')])
def test_uncertain_low_confidence_and_protective_results_excluded(analyzed, field, value):
    for content in analyzed['contents']:
        if content['user_id'] == 'u01':
            content[field] = value
    assert score(analyzed, 'u01')['overall_score'] == 0


def test_duplicate_evidence_same_incident_not_added(analyzed):
    before = score(analyzed, 'u14')['overall_score']
    interaction = deepcopy(next(i for i in analyzed['interactions'] if i['user_id'] == 'u14'))
    interaction['interaction_id'] = 'duplicate-share'
    analyzed['interactions'].append(interaction)
    result = score(analyzed, 'u14')
    assert result['overall_score'] == before
    assert sum(bool(e['contributions']) for e in result['evidence']) == 1


def test_comments_in_same_thread_count_once_per_category(analyzed):
    assert score(analyzed, 'u06')['distinct_incidents']['HARASSMENT'] == 1


@pytest.mark.parametrize('number,expected', [(0,'Low'), (24,'Low'), (25,'Moderate'), (49,'Moderate'), (50,'High'), (74,'High'), (75,'Critical'), (100,'Critical')])
def test_thresholds(number, expected):
    assert band(number) == expected


def test_unprocessed_is_not_claimed_safe(store):
    result = score(store.snapshot(), 'u01')
    assert result['overall_score'] == 0
    assert result['analysis_complete'] is False


def test_contributions_reproduce_category_totals(analyzed):
    for user in analyzed['users']:
        result = score(analyzed, user['user_id'])
        for category, total in result['category_scores'].items():
            assert total == round(min(100, sum(e['contributions'].get(category, 0) for e in result['evidence'])), 2)
