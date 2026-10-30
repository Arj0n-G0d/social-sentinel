# Social Sentinel — Project Progress Report

**Reporting date:** 24 October 2026  
**Current stage:** Prototype for contextual social-media analysis and knowledge-graph construction
**Group:** Arjun Gaur, Atul, Anirudh Singh, Arpan Vijay Pethkar

## 1. Project Overview

Social Sentinel is a project for representing social-media activity as a knowledge graph and analyzing the meaning of content within its interaction context. It connects users, posts, comments, and reactions so that activity can be examined as part of a conversation rather than as isolated text.

The project addresses a central challenge in social-media analysis: discussing harmful content does not necessarily mean supporting it. A comment may condemn an abusive post, a share may warn others about a threat, and a quotation may document an incident. Social Sentinel classifies each message into hate, harassment, violence, spam, or benign, using conversation context to interpret the message itself.

Work so far establishes the data structures, synthetic demonstration dataset, graph-storage layer, configurable language-model classification, and analysis workflow. The longer-term objective is a self-sustaining application that can start with selected users, discover relevant activity and participants, and progressively build and update its graph.

## 2. Project Objectives

- Model users, content, and interactions in a connected graph.
- Analyze posts, comments, and interactions using relevant conversational context.
- Produce structured classifications with four category probabilities and optional explanations.
- Avoid repeating successful analysis when its inputs have not changed.
- Establish a foundation for autonomous data collection and explainable user-level assessment.

## 3. Progress Achieved So Far

### 3.1 Structured data models

Validated Python models have been defined using Pydantic. They represent users, posts and comments, likes, dislikes, shares, datasets, and analysis results.

Each content record includes its author, text, timestamp, type, and an optional parent reference. Each interaction identifies the acting user, the target content, the interaction type, and any accompanying share text. These references preserve the relationships needed to reconstruct conversations and activity.

Classification results contain the following fields:

| Field | Purpose |
| --- | --- |
| Category | Identifies topics such as hate, harassment, violence, spam, or benign content |
| Probabilities | Independent values between 0 and 1 for hate, harassment, violence, and spam |
| Explanation | Provides a short reason for the classification |

### 3.2 Reproducible synthetic dataset

A deterministic demonstration dataset has been prepared with **20 fictional users, 39 posts and comments, and 42 interactions (likes/dislikes/shares)**. It includes harmful expression, threats, ordinary discussion, counter-speech, reporting, shares, and reactions.

### 3.3 Neo4j knowledge-graph storage

The graph-storage layer models three node types: `User`, `Content`, and `Interaction`. Four relationship types connect them:

| Relationship | Meaning |
| --- | --- |
| `CREATED` | A user authored a post or comment |
| `REPLIES_TO` | A comment responds to another content item |
| `PERFORMED` | A user performed an interaction |
| `TARGETS` | An interaction refers to a content item |

### 3.4 Context-aware content classification

A configurable classification layer supports Gemini, a Chat Completions-compatible provider, and a deterministic mock provider for testing purposes.

The workflow constructs context according to the item being analyzed:

- A post supplies its own text.
- A comment supplies its text and its immediate parent's text.
- An interaction supplies its type, accompanying text, target text, and the target's immediate parent text when available.

The classifier evaluates the message itself using parent or target text as context. Warnings and factual reports are benign unless their own text contains harmful content. Models return four category probabilities and an optional explanation. The application derives categories independently at probability ≥0.5, using benign if none qualifies.

User identity and graph associations are excluded from the classification context. This keeps the interpretation focused on the message and the conversation supplied with it.

### 3.5 Analysis persistence and caching

Validated classifications are stored as properties on the relevant graph nodes together with provider, model, analysis version, status, explanation, and raw validated output.

Each analysis receives a fingerprint derived from the item text, supplied context, provider configuration, model, instructions, schema, and analysis version. Successful results are reused when the fingerprint remains unchanged. Changes to those inputs trigger fresh analysis (caching to prevent repeated LLM calls).

## 4. Current Scope and Limitations

The current foundation operates on a controlled synthetic dataset (which we created ourselves).

## 5. Future Prospects (Under Progress)

### 5.1 Self-sustaining graph construction

The main future goal is to allow an operator to point the application at a small set of seed users. The application would collect their accessible activity, identify people interacting with their posts, and incrementally expand the graph.

The proposed operating cycle is:

1. **Select seed users:** Configure initial accounts to keep an eye on.
2. **Collect activity:** Retrieve accessible posts, comments, shares, and available interaction information through supported platform integrations.
3. **Discover participants:** Identify authors and other users appearing in those interactions and add eligible accounts to a discovery queue.
4. **Update the graph:** Insert new records, connect activity to existing nodes, and update changed records using stable platform identifiers.
5. **Analyze changes:** Classify new or modified items and reconsider affected context using fingerprints.
6. **Refresh continuously:** Repeat collection from saved checkpoints so the system can operate without manually supplying each dataset.

The streaming worker starts with suspicious users, fetches recent activity once for the original seed accounts, and then consumes Jetstream WebSocket events. Direct comments, likes and reposts on watched posts introduce new pool members, with an account cap and bounded initial listings. Replies to comments are not explored. Reconnects resume from an in-memory cursor. A scheduler, work queue, retry policy, and collection checkpoints would make this cycle recoverable and manageable. Available platform data would determine which interactions can actually be collected.

Discovery and assessment would remain separate decisions: an account can enter the graph because it interacted with a seed user's post, while any assessment of that account would depend on its own observable behavior.

### 5.2 Explainable risk engine and user scores

A risk engine would convert eligible item-level classifications into transparent user-level review scores. Separate scores would be maintained for hate, harassment, violence, and spam.

An initial scoring design is:

```text
Item points for a category = 36 × category probability × role weight

Category score = min(100, sum of all eligible item points in that category)

Overall user score = maximum of the category scores
```

Proposed role weights are **1.0 for authored posts and comments** and **0.75 for shares classified as harmful**. An item would qualify only when analysis succeeds, the category probability is at least **0.5**.

Benign categories, probabilities below the threshold, likes/dislikes, and unsuccessful analysis contribute zero. A user's connections or another person's behavior would not increase their score.

For example, an eligible authored item with hate probability 0.90 contributes
`36 × 0.90 × 1.0 = 32.4` hate points. An eligible share with the same probability
contributes 24.3 points. Both count separately, even when they refer to the same
post. Category totals cap at 100. Repeated copies of the same record ID count
once. Scoring does not group items by incident or traverse parent threads.
