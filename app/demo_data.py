"""Deterministic fictional fixtures. Expected labels NEVER enter real LLM prompts."""
from app.schemas import Classification, Content, ActivityBatch, Interaction, User


def _generate_demo_fixture() -> tuple[ActivityBatch, dict[str, Classification]]:
    users = [User(user_id=f'u{i:02}', display_name=f'Fictional {i:02}') for i in range(1, 21)]
    contents, interactions, expected = [], [], {}
    timestamp = '2026-01-01T12:00:00Z'

    def label(category=None, probability=0.95, explanation=None):
        return Classification(
            probabilities={name: probability if name in (category or []) else 0.05
                           for name in ('HATE', 'HARASSMENT', 'VIOLENCE', 'SPAM')},
            explanation=explanation or 'Synthetic expected probability fixture.')

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
        if classification is None:
            target_label = expected[target]
            classification = label(
                target_label.category.copy(), probability=0.4,
                explanation='Synthetic reaction: probabilities below threshold; no authored text.')
        expected[iid] = classification

    # Fictional group names, no real personal information or protected-group slurs.
    hate = post(1, 'All members of the fictional Zorvian ethnic group are vermin and should be excluded.', label(['HATE']))
    post(1, 'Zorvian people are inherently inferior; deny them housing.', label(['HATE']))
    post(1, 'Keep every Zorvian family out of our schools because of their ethnicity.', label(['HATE']))
    post(1, 'No Zorvian should be allowed to work here because of their ethnicity.', label(['HATE']))
    benign = post(10, 'Our fictional community garden opens on Saturday. Everyone is welcome!')
    post(2, 'Fictional 10, I will come to the garden Saturday and seriously injure you.', label(['VIOLENCE']), benign)
    post(3, 'This attack on Zorvians is wrong. Everyone deserves respect.', label(), hate)
    post(6, 'Fictional 10, you are worthless. I will keep humiliating you every day.', label(['HARASSMENT']), benign)
    post(6, 'Fictional 10, another day to mock you: nobody wants you here.', label(['HARASSMENT']), benign)
    for day in range(5):
        post(7, f'Unsolicited promotion round {day+1}: buy fake miracle tokens at fake-shop.invalid NOW!', label(['SPAM']))
    post(8, 'I have reported the anti-Zorvian post to the moderators.', label(), hate)
    post(9, 'This post contains a threat; moderators should review it.', label(), benign)
    post(11, 'I will destroy you at chess tonight! Looking forward to our friendly match.')
    post(12, 'The history seminar discusses how violent propaganda spreads without endorsing it.')
    post(13, 'Sure, whatever that means.', label(), hate)
    for i in range(20):
        post(10 + i % 11, f'Garden diary {i+1}: we planted flowers and shared lunch with our neighbors.')
    
    interact(4, hate, 'like')
    interact(5, hate, 'share', 'Warning: this is hateful propaganda. Do not support it.', label())
    interact(14, hate, 'share', 'I agree with excluding Zorvians. Spread this message!', label(['HATE']))
    interact(15, hate, 'share')
    interact(16, hate, 'share', 'For the moderation report: documenting this hateful message.', label())
    interact(17, hate, 'share', 'I oppose this bigotry. Please challenge it.', label())
    
    for content in contents[:18]:
        interact(20, content.content_id, 'like')
        interact(18, content.content_id, 'dislike')
    return ActivityBatch(users=users, contents=contents, interactions=interactions), expected


def generate_demo_data() -> ActivityBatch:
    """Return raw activity only, without expected testing labels."""
    activity, _ = _generate_demo_fixture()
    return activity


def generate_demo_labels() -> dict[str, Classification]:
    """Expected probabilities for mock replay and tests, separate from activity."""
    _, expected = _generate_demo_fixture()
    return expected
