import os
from pathlib import Path
from functools import lru_cache
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Application settings."""

    app_name: str = "RAG API"
    app_version: str = "1.0.0"
    debug: bool = Field(default=False, validation_alias="DEBUG")

    api_host: str = Field(default="0.0.0.0", validation_alias="API_HOST")
    api_port: int = Field(default=8000, validation_alias="API_PORT")

    openai_api_key: str = Field(default="", validation_alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", validation_alias="OPENAI_MODEL")
    anthropic_api_key: str = Field(default="", validation_alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(
        default="claude-3-5-sonnet-20241022", validation_alias="ANTHROPIC_MODEL"
    )
    openai_embedding_model: str = Field(
        default="text-embedding-3-small", validation_alias="OPENAI_EMBEDDING_MODEL"
    )

    vector_store_type: str = Field(
        default="chroma", validation_alias="VECTOR_STORE_TYPE"
    )
    chroma_host: str = Field(default="localhost", validation_alias="CHROMA_HOST")
    chroma_port: int = Field(default=8001, validation_alias="CHROMA_PORT")
    chroma_collection_name: str = Field(
        default="documents", validation_alias="CHROMA_COLLECTION_NAME"
    )
    chroma_persist_dir: str = Field(
        default="./data/chroma", validation_alias="CHROMA_PERSIST_DIR"
    )

    chunk_size: int = Field(default=1000, validation_alias="CHUNK_SIZE")
    chunk_overlap: int = Field(default=200, validation_alias="CHUNK_OVERLAP")

    similarity_top_k: int = Field(default=5, validation_alias="SIMILARITY_TOP_K")
    similarity_threshold: float = Field(
        default=0.7, validation_alias="SIMILARITY_THRESHOLD"
    )

    data_dir: Path = Field(default=Path("./data"), validation_alias="DATA_DIR")

    cors_origins: list[str] = Field(default=["*"], validation_alias="CORS_ORIGINS")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


settings = get_settings()
