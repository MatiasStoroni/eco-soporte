import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    google_api_key: str = ""
    llm_generator_model: str = "google_genai:gemini-3.5-flash"
    llm_grader_model: str = "google_genai:gemini-3.5-flash-lite"
    # Nivel de razonamiento (Gemini 3+: minimal | low | medium | high). Vacío = el del modelo.
    # Generador en low: con medium (el default de gemini-3.5-flash) cada generación tarda 4-11 s; minimal no
    # es más rápido que low y produce más borradores rechazados por el verificador (medido 2026-10-05).
    llm_generator_thinking: str = "low"
    llm_grader_thinking: str = ""
    # Reformateo de documentos (eco_kb.ingest.restructure). Vacío = el modelo del generador.
    llm_restructure_model: str = ""
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

    # Contraseña del panel de administración (/admin/*). Fácil a propósito (demo); vacía = panel desactivado.
    admin_token: str = "admin"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.google_api_key:
        os.environ.setdefault("GOOGLE_API_KEY", s.google_api_key)
    return s
