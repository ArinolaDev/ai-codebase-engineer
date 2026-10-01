from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Anthropic (optional for now — we won't touch this until Phase 4+)
    anthropic_api_key: str | None = None

    # Database
    database_url: str = "postgresql://postgres:postgres@localhost:5432/codebase_engineer"

    # Vector store
    chroma_persist_dir: str = "./data/indexes"

    # App
    app_env: str = "development"
    data_dir: str = "./data/repos"

    # CORS
    allowed_origins: str = "http://localhost:5173"

    @property
    def allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",")]


settings = Settings()