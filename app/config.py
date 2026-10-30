from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    neo4j_uri: str = ''
    neo4j_user: str = ''
    neo4j_password: str = ''
    neo4j_database: str = ''
    llm_provider: Literal['mock', 'chat_completions', 'gemini'] = 'mock'
    llm_base_url: str = ''
    llm_api_key: str = ''
    llm_model: str = ''
    analysis_version: str = 'v1'
    llm_timeout: float = 120
