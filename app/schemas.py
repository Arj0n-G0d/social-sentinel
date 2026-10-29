from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Category = Literal['HATE', 'HARASSMENT', 'VIOLENCE', 'SPAM', 'BENIGN', 'UNCERTAIN']
Intent = Literal['ENDORSEMENT', 'CONDEMNATION', 'NEUTRAL', 'UNCERTAIN']

class Classification(BaseModel):
    model_config = ConfigDict(extra='forbid')
    category: Annotated[list[Category], Field(min_length=1, max_length=6)]
    severity: Annotated[int, Field(strict=True, ge=0, le=3)]
    confidence: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    intent: Intent
    explanation: Annotated[str, Field(min_length=1, max_length=1200)]

    @model_validator(mode='after')
    def consistent_categories(self):
        if len(set(self.category)) != len(self.category):
            raise ValueError('Duplicate categories')
        if set(self.category) & {'BENIGN', 'UNCERTAIN'} and len(self.category) > 1:
            raise ValueError('Non-harmful or uncertain categories must stand alone')
        return self


class User(BaseModel):
    user_id: str
    display_name: str
    is_synthetic: bool = True


class Content(BaseModel):
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


class Dataset(ActivityBatch):
    expected: dict[str, Classification]


class AnalysisResult(BaseModel):
    classification: Classification
    provider: str # 'mock' or 'chat-completions'
    model: str # 'fixture-replay-v3' (for mock) or our configured model
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
