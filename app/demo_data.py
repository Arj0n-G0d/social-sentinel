"""Deterministic fictional fixtures. Expected labels NEVER enter real LLM prompts."""
from app.schemas import Classification, Content, ActivityBatch, Interaction, User


def generate_demo_data() -> ActivityBatch:
    users = [User(user_id=f'u{i:02}', display_name=f'Fictional {i:02}') for i in range(1, 21)]
    contents, interactions, expected = [], [], {}
    timestamp = '2026-01-01T12:00:00Z'

    def label(category=None, severity=0, intent='NEUTRAL', confidence=0.95, explanation=None):
        return Classification(category=['BENIGN'] if category is None else category, severity=severity, confidence=confidence,
                              intent=intent, explanation=explanation or f'Synthetic expected fixture: {intent.lower()} in context.')

    def post(user, text, classification=None, parent=None):
        cid = f'c{len(contents) + 1:03}'
        contents.append(Content(content_id=cid, user_id=f'u{user:02}',
                                content_type='comment' if parent else 'post', text=text,
                                created_at=timestamp, parent_id=parent))
        expected[cid] = classification or label()
        return cid

    def interact(user, target, kind, text='', classification=None):
        iid = f'i{len(interactions) + 1:03}'
        interactions.append(Interaction(interaction_id=iid, user_id=f'u{user:02}',
                                        target_id=target, interaction_type=kind, text=text,
                                        created_at=timestamp))
        """
                ASSUMPTION
                        A like to some post -> same category, same intent but severity 1
                        A dislike to some post -> same category, opposite intent, but severity 1
                        NEUTRAL / UNCERTAIN intent in dislike remains unchanged.
        """
        if classification is None and kind in {'like', 'dislike'}:
            target_label = expected[target]
            intent = target_label.intent
            if kind == 'dislike':
                intent = {'ENDORSEMENT': 'CONDEMNATION',
                          'CONDEMNATION': 'ENDORSEMENT'}.get(intent, intent)
            classification = label(
                target_label.category.copy(), severity=1, intent=intent,
                confidence=0.4 if intent == 'UNCERTAIN' else 0.7,
                explanation=f'Synthetic {kind} rule: same target categories, '
                            f'{"same" if kind == "like" else "opposite"} stance, severity 1. '
                            'NEUTRAL and UNCERTAIN remain unchanged.')
        expected[iid] = classification or label(['UNCERTAIN'], intent='UNCERTAIN', confidence=0.4)

    # Fictional group names, no real personal information or protected-group slurs.
    hate = post(1, 'All members of the fictional Zorvian ethnic group are vermin and should be excluded.', label(['HATE'], 3, 'ENDORSEMENT'))
    post(1, 'Zorvian people are inherently inferior; deny them housing.', label(['HATE'], 3, 'ENDORSEMENT'))
    post(1, 'Keep every Zorvian family out of our schools because of their ethnicity.', label(['HATE'], 3, 'ENDORSEMENT'))
    post(1, 'No Zorvian should be allowed to work here because of their ethnicity.', label(['HATE'], 3, 'ENDORSEMENT'))
    benign = post(10, 'Our fictional community garden opens on Saturday. Everyone is welcome!')
    post(2, 'Fictional 10, I will come to the garden Saturday and seriously injure you.', label(['VIOLENCE'], 3, 'ENDORSEMENT'), benign)
    post(3, 'This attack on Zorvians is wrong. Everyone deserves respect.', label(['HATE'], intent='CONDEMNATION'), hate)
    post(6, 'Fictional 10, you are worthless. I will keep humiliating you every day.', label(['HARASSMENT'], 2, 'ENDORSEMENT'), benign)
    post(6, 'Fictional 10, another day to mock you: nobody wants you here.', label(['HARASSMENT'], 2, 'ENDORSEMENT'), benign)
    for day in range(5):
        post(7, f'Unsolicited promotion round {day+1}: buy fake miracle tokens at fake-shop.invalid NOW!', label(['SPAM'], 2, 'ENDORSEMENT'))
    post(8, 'I have reported the anti-Zorvian post to the moderators.', label(['HATE'], intent='NEUTRAL'), hate)
    post(9, 'This post contains a threat; moderators should review it.', label(['VIOLENCE'], intent='NEUTRAL'), benign)
    post(11, 'I will destroy you at chess tonight! Looking forward to our friendly match.')
    post(12, 'The history seminar discusses how violent propaganda spreads without endorsing it.')
    post(13, 'Sure, whatever that means.', label(['UNCERTAIN'], intent='UNCERTAIN', confidence=0.4), hate)
    for i in range(20):
        post(10 + i % 11, f'Garden diary {i+1}: we planted flowers and shared lunch with our neighbors.')
    
    interact(4, hate, 'like')
    interact(5, hate, 'share', 'Warning: this is hateful propaganda. Do not support it.', label(['HATE'], intent='CONDEMNATION'))
    interact(14, hate, 'share', 'I agree with excluding Zorvians. Spread this message!', label(['HATE'], 3, 'ENDORSEMENT'))
    interact(15, hate, 'share')
    interact(16, hate, 'share', 'For the moderation report: documenting this hateful message.', label(['HATE'], intent='NEUTRAL'))
    interact(17, hate, 'share', 'I oppose this bigotry. Please challenge it.', label(['HATE'], intent='CONDEMNATION'))
    
    for content in contents[:18]:
        interact(20, content.content_id, 'like')
        interact(18, content.content_id, 'dislike')
    return ActivityBatch(users=users, contents=contents, interactions=interactions)
