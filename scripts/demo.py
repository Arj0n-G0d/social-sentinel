"""Run the actual Neo4j-backed API workflow and print representative profiles."""
import argparse
import json
import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://localhost:8000')
    args = parser.parse_args()
    with httpx.Client(base_url=args.url, timeout=7200) as client:
        for route in ('/ingest-demo', '/analyze'):
            response = client.post(route)
            response.raise_for_status()
            print(route, json.dumps(response.json(), indent=2))
        for uid in ('u01', 'u02', 'u03', 'u04', 'u05', 'u14', 'u20'):
            response = client.get(f'/users/{uid}')
            response.raise_for_status()
            user = response.json()
            print(json.dumps({k: user[k] for k in ('user_id', 'overall_score', 'review_level', 'category_scores')}, indent=2))
        response = client.get('/graph/u05')
        response.raise_for_status()
        print('Warning share neighborhood:', json.dumps(response.json(), indent=2))


if __name__ == '__main__':
    main()
