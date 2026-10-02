import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    google_api_key: str = ""
    llm_generator_model: str = "google_genai:gemini-3.5-flash"
    llm_grader_model: str = "google_genai:gemini-3.5-flash-lite"
    embedding_model: str = "gemini-embedding-001"
    embedding_dim: int = 768

    database_url_admin: str = "postgresql://ecokb:ecokb@localhost:5433/ecokb"
    database_url_support_ro: str = "postgresql://rag_support_ro:support_ro_dev@localhost:5433/ecokb"
    database_url_sales_ro: str = "postgresql://rag_sales_ro:sales_ro_dev@localhost:5433/ecokb"

    min_vector_score: float = 0.45
    top_k: int = 8
    candidate_k: int = 20
    max_ret: int = 2
    max_gen: int = 3

    clients_path: str = "config/clients.yaml"
    safety_path: str = "config/safety.yaml"
    handoff_path: str = "config/handoff.yaml"
    kb_dir: str = "kb"

    # Google Drive (fuente de la KB en producción)
    drive_root_folder_id: str = ""
    google_oauth_client_secret_path: str = "secrets/google_client_secret.json"
    google_oauth_token_path: str = "secrets/google_token.json"
    drive_sync_interval_minutes: int = 15

    # Panel de administración (/admin/*). Vacío = panel desactivado.
    admin_token: str = ""


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.google_api_key:
        os.environ.setdefault("GOOGLE_API_KEY", s.google_api_key)
    return s
