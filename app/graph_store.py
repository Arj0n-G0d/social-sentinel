import json
from datetime import datetime, timezone

from neo4j import GraphDatabase
from app.config import Settings
from app.schemas import ActivityBatch, AnalysisResult, HARMFUL_CATEGORIES


class GraphStore:
    def __init__(self, settings: Settings):
        self.driver = GraphDatabase.driver(settings.neo4j_uri,
                                           auth=(settings.neo4j_user, settings.neo4j_password))
        self.database = settings.neo4j_database

    def close(self):
        self.driver.close()

    def initialize(self):
        self.driver.verify_connectivity()
        # Labels and property names are static application constants, never user input.
        # Ensures IDs are unique.
        for label, key in [('User', 'user_id'), ('Content', 'content_id'), ('Interaction', 'interaction_id')]:
            self.query(f'CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS '
                       f'FOR (n:{label}) REQUIRE n.{key} IS UNIQUE')

        # Invalidate legacy analyses so they are reclassified under the new schema.
        self.query('''MATCH (n) WHERE (n:Content OR n:Interaction) AND (n.intent IS NOT NULL OR n.severity IS NOT NULL OR n.confidence IS NOT NULL)
                      REMOVE n.intent, n.category, n.severity, n.confidence, n.explanation,
                             n.fingerprint, n.status, n.raw_output, n.analysis_error''')

    def query(self, cypher, **params):
        records, _, _ = self.driver.execute_query(cypher, parameters_=params, database_=self.database)
        return [dict(r) for r in records]

    # ingest() takes a dataset of users, content, and interactions and saves them into Neo4j, connecting them together.
    def ingest(self, dataset: ActivityBatch):
        def write(tx):
            tx.run('''UNWIND $rows AS row MERGE (u:User {user_id: row.user_id})
                      SET u += row''', rows=[u.model_dump() for u in dataset.users]).consume()
            tx.run('''UNWIND $rows AS row
                      MATCH (u:User {user_id: row.user_id})
                      MERGE (c:Content {content_id: row.content_id}) SET c += row
                      MERGE (u)-[:CREATED]->(c)''',
                   rows=[c.model_dump(exclude_none=True) for c in dataset.contents]).consume()
            tx.run('''UNWIND $rows AS row
                      MATCH (c:Content {content_id: row.content_id})
                      MATCH (p:Content {content_id: row.parent_id})
                      MERGE (c)-[:REPLIES_TO]->(p)''',
                   rows=[c.model_dump() for c in dataset.contents if c.parent_id]).consume()
            tx.run('''UNWIND $rows AS row
                      MATCH (u:User {user_id: row.user_id})
                      MATCH (c:Content {content_id: row.target_id})
                      MERGE (i:Interaction {interaction_id: row.interaction_id}) SET i += row
                      MERGE (u)-[:PERFORMED]->(i) MERGE (i)-[:TARGETS]->(c)''',
                   rows=[i.model_dump() for i in dataset.interactions]).consume()
        with self.driver.session(database=self.database) as session:
            session.execute_write(write)
        return {'users': len(dataset.users), 'contents': len(dataset.contents),
                'interactions': len(dataset.interactions)}

    @staticmethod
    def _properties(node):
        properties = dict(node)
        if 'score_summary' in properties:
            properties.update(json.loads(properties.pop('score_summary')))
        probabilities = {c: properties.pop(f'probability_{c}') for c in HARMFUL_CATEGORIES
                         if f'probability_{c}' in properties}
        if probabilities:
            properties['probabilities'] = probabilities
        return properties

    def snapshot(self):
        # Read the small MVP dataset within one transaction (Neo4j read-committed isolation).
        def read(tx):
            result = {}
            for label, key in [('User', 'users'), ('Content', 'contents'), ('Interaction', 'interactions')]:
                result[key] = [self._properties(r['n']) for r in tx.run(f'MATCH (n:{label}) RETURN n')]
            return result
        with self.driver.session(database=self.database) as session:
            return session.execute_read(read)

    def save_analysis(self, label: str, ident: str, result: AnalysisResult):
        if label not in {'Content', 'Interaction'}:
            raise ValueError('Unsupported node label')
        key = 'content_id' if label == 'Content' else 'interaction_id'
        properties = result.model_dump(exclude={'classification', 'error'})
        classification = result.classification.model_dump()
        probabilities = classification.pop('probabilities')
        properties.update(classification)
        properties.update({f'probability_{c}': value for c, value in probabilities.items()})
        properties['analysis_error'] = result.error or ''
        self.query(f'MATCH (n:{label} {{{key}: $ident}}) SET n += $properties REMOVE n.intent, n.severity, n.confidence',
                   ident=ident, properties=properties)

    def save_user_scores(self, user_id: str, scores: dict):
        """Neo4j properties cannot hold maps, so keep a JSON summary and scalars."""
        summary = {key: value for key, value in scores.items() if key != 'evidence'}
        properties = {key: summary[key] for key in (
            'overall_score', 'review_level', 'flag_for_review', 'analysis_complete',
            'contains_mock_analysis')}
        properties.update({f'category_score_{category}': value
                           for category, value in summary['category_scores'].items()})
        properties['score_summary'] = json.dumps(summary)
        properties['scores_updated_at'] = datetime.now(timezone.utc).isoformat()
        self.query('MATCH (u:User {user_id: $user_id}) SET u += $properties',
                   user_id=user_id, properties=properties)

    def delete_record(self, label: str, ident: str):
        """Remove a streamed record, including reactions targeting a deleted post."""
        if label not in {'Content', 'Interaction'}:
            raise ValueError('Unsupported deletion label')
        key = 'content_id' if label == 'Content' else 'interaction_id'

        def write(tx):
            if label == 'Content':
                tx.run('MATCH (i:Interaction {target_id: $ident}) DETACH DELETE i',
                       ident=ident).consume()
            tx.run(f'MATCH (n:{label} {{{key}: $ident}}) DETACH DELETE n',
                   ident=ident).consume()
        with self.driver.session(database=self.database) as session:
            session.execute_write(write)

    def neighborhood(self, user_id):
        """
                Starting from the user, follow outgoing CREATED, PERFORMED, or TARGETS relationships up to two steps, returning at most 80 paths. 
                For example:
                        User ──CREATED──> Post
                        User ──PERFORMED──> Like ──TARGETS──> Post
        """
        records = self.query('''MATCH (u:User {user_id: $user_id})
            MATCH p=(u)-[:CREATED|PERFORMED|TARGETS*1..2]->(n)
            RETURN p LIMIT 80''', user_id=user_id)
        # Check whether the user exists
        user = self.query('MATCH (u:User {user_id: $user_id}) RETURN u', user_id=user_id)
        if not user:
            return None
        nodes, edges = {}, {}

        def node_id(node):
            label = next(iter(node.labels))
            key = {'User': 'user_id', 'Content': 'content_id', 'Interaction': 'interaction_id'}[label]
            return f'{label}:{node[key]}'

        def add_node(node):
            label = next(iter(node.labels))
            nid = node_id(node)
            nodes[nid] = {'id': nid, 'label': label, 'properties': self._properties(node)}
            return nid

        def add_path(path):
            for node in path.nodes:
                add_node(node)
            for rel in path.relationships:
                edge = {'source': node_id(rel.start_node), 'target': node_id(rel.end_node), 'type': rel.type}
                edges[edge['source'], edge['type'], edge['target']] = edge
        # Dictionaries prevent the same node or connection from appearing multiple times.

        add_node(user[0]['u']) # Adding the user under consideration first
        for row in records:
            add_path(row['p'])
        # Get all content_ids of nodes of Content type
        content_ids = [n['properties']['content_id'] for n in nodes.values() if n['label'] == 'Content']
        """
                If any collected content replies to another post or comment, 
                also include that immediate parent and its REPLIES_TO connection. 
                This query returns at most another 80 paths.
        """
        for row in self.query('''MATCH p=(c:Content)-[:REPLIES_TO]->(:Content)
                                WHERE c.content_id IN $ids RETURN p LIMIT 80''', ids=content_ids):
            add_path(row['p'])
        return {'nodes': list(nodes.values()), 'edges': list(edges.values()),
                'path_limit': 80, 'note': 'Bounded outgoing neighborhood plus immediate parent context.'}
