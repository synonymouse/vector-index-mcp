# vector_index_mcp/config.py
import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List


class Settings(BaseSettings):
    _env_file_value = None if os.getenv("TESTING_MODE") == "true" else ".env"
    model_config = SettingsConfigDict(
        env_file=_env_file_value, env_file_encoding="utf-8", extra="ignore"
    )

    project_path: str = os.getcwd()
    lancedb_uri: str = ".lancedb"
    embedding_model_name: str = "all-MiniLM-L6-v2"
    log_level: str = "INFO"
    chunk_size: int = 1000
    chunk_overlap: int = 200
    ignore_patterns: List[str] = [
        ".*",
        "*.db",
        "*.sqlite",
        "*.log",
        "node_modules/*",
        "venv/*",
        ".git/*",
    ]
    watch_delay: float = 1.0  # seconds
    debounce_period: float = 0.5  # seconds

    @property
    def embedding_dim(self) -> int:
        # This is a common dimension for many sentence-transformer models.
        # A more robust solution might involve inspecting the model, but this is often fixed.
        if self.embedding_model_name == "all-MiniLM-L6-v2":
            return 384
        elif self.embedding_model_name == "all-mpnet-base-v2":
            return 768
        # Add other models as needed
        raise ValueError(
            f"Unknown embedding dimension for model: {self.embedding_model_name}"
        )


_settings_instance = None


def get_vector_index_settings() -> Settings:
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()

        env_project_path = os.getenv("PROJECT_PATH")
        if env_project_path:
            _settings_instance.project_path = os.path.abspath(env_project_path)
        else:
            _settings_instance.project_path = os.path.abspath(
                _settings_instance.project_path
            )

        env_lancedb_uri = os.getenv("LANCEDB_URI")
        if env_lancedb_uri:
            if not os.path.isabs(env_lancedb_uri):
                _settings_instance.lancedb_uri = os.path.join(
                    _settings_instance.project_path, env_lancedb_uri
                )
            else:
                _settings_instance.lancedb_uri = env_lancedb_uri
        else:
            if not os.path.isabs(_settings_instance.lancedb_uri):
                _settings_instance.lancedb_uri = os.path.join(
                    _settings_instance.project_path, _settings_instance.lancedb_uri
                )

        env_log_level = os.getenv("LOG_LEVEL")
        if env_log_level:
            _settings_instance.log_level = env_log_level.upper()

    return _settings_instance
