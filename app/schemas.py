from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, computed_field

Category = Literal['HATE', 'HARASSMENT', 'VIOLENCE', 'SPAM', 'BENIGN']

HARMFUL_CATEGORIES = ('HATE', 'HARASSMENT', 'VIOLENCE', 'SPAM')
CATEGORY_THRESHOLD = 0.5
Probability = Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]


class CategoryProbabilities(BaseModel):
    """Independent probabilities; they do not need to sum to one."""
    model_config = ConfigDict(extra='forbid')
    HATE: Probability
    HARASSMENT: Probability
    VIOLENCE: Probability
    SPAM: Probability


class Classification(BaseModel):
    model_config = ConfigDict(extra='forbid')
    probabilities: CategoryProbabilities
    explanation: Annotated[str, Field(max_length=1200)] = ''

    @computed_field
    @property
    def category(self) -> list[Category]:
        matched = [category for category, probability in self.probabilities.model_dump().items()
                   if probability >= CATEGORY_THRESHOLD]
        return matched or ['BENIGN']


class User(BaseModel):
    """Live user IDs are native IDs accepted directly by the platform API."""
    user_id: str
    display_name: str
    is_synthetic: bool = True


class Content(BaseModel):
    """Live content, author, and parent IDs are native API-ready IDs."""
    content_id: str
    user_id: str
    content_type: Literal['post', 'comment']
    text: str
    created_at: str
    parent_id: str | None = None

class Interaction(BaseModel):
    interaction_id: str
    user_id: str
    target_id: str
    interaction_type: Literal['like', 'share', 'dislike']
    created_at: str
    text: str = '' # Stores the message added when sharing a post

class ActivityBatch(BaseModel):
    """Platform-independent ingestion records, also used by the classifier workflow."""
    users: list[User]
    contents: list[Content]
    interactions: list[Interaction]


class AnalysisResult(BaseModel):
    classification: Classification
    provider: str # 'mock' or 'gemini'
    model: str # 'fixture-replay-v5' (for mock) or our configured model
    """ 
        We keep provider and model because the same connection mode can call 
        different models, and we need to know which produced each result 
    """
    analysis_version: str # 'v1' (change when you need fresh results without changing any config)
    fingerprint: str 
    """
        Hased value
        It includes:
                - The text and its parent/target context.
                - The provider, endpoint, and model.
                - The classifier instructions (prompt).
                - The expected response schema.
                - The analysis version.
        We use it to answer: “Have we already analyzed this exact input with these settings?”
    """
    status: Literal['complete', 'error'] = 'complete'
    error: str | None = None
    raw_output: str = ''
