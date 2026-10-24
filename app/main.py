from contextlib import asynccontextmanager
import logging
from threading import Lock
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from neo4j.exceptions import Neo4jError, ServiceUnavailable, SessionExpired
from app.config import Settings
from app.graph_store import GraphStore
from app.llm_classifier import Classifier
from app.routes import router


def create_app(settings=None, store=None, classifier=None):
    @asynccontextmanager
    async def lifespan(app):
        config = settings or Settings()
        app.state.classifier = classifier or Classifier(config)
        app.state.store = store or GraphStore(config)
        app.state.mutation_lock = Lock()
        try:
            app.state.store.initialize()
            yield
        finally:
            app.state.store.close()

    app = FastAPI(title='Social Sentinel', version='0.1.0', lifespan=lifespan,
                  description='Synthetic behavioral evidence and human-review demonstration. '
                              'Mock results are explicitly identified. No validated moderation judgments.')
    app.include_router(router)

    async def database_error(request: Request, exc: Exception):
        logging.getLogger(__name__).warning('Database operation failed: %s', type(exc).__name__)
        return JSONResponse(status_code=503, content={'detail': 'Neo4j unavailable or query failed. Check database configuration and logs.'})

    for exception in (Neo4jError, ServiceUnavailable, SessionExpired):
        app.add_exception_handler(exception, database_error)
    return app


app = create_app()
