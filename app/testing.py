from app.demo_data import generate_demo_data
from app.graph_store import GraphStore
from app.llm_classifier import Classifier, GeminiProvider
from app.config import Settings
from app.workflow import analyze_store

settings = Settings()
seedDataset = generate_demo_data()
graphStore = GraphStore(settings)
geminiProvider = GeminiProvider(settings)
classifier = Classifier(settings, geminiProvider)

# Seeding the DB
# graphStore.initialize()
# graphStore.ingest(seedDataset)

# Analyzing the content
# analyze_store(graphStore, classifier)
