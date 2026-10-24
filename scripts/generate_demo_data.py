"""Run from the project root: python -m scripts.generate_demo_data."""
import json
from app.demo_data import generate_demo_data

if __name__ == '__main__':
    print(json.dumps(generate_demo_data().model_dump(), indent=2))
