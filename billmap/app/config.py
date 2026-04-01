"""BillMap configuration — switches between local (SQLite) and SaaS (Postgres) modes."""

from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    mode: str = "local"  # "local" or "saas"
    app_name: str = "BillMap"
    debug: bool = True

    # Database
    database_url: str = ""  # Auto-set based on mode if empty

    # File storage
    upload_dir: str = ""  # Auto-set based on mode if empty

    # LLM
    llm_provider: str = ""  # "anthropic", "openai", or ""
    llm_api_key: str = ""
    llm_model: str = ""  # e.g. "claude-sonnet-4-20250514", "gpt-4o"

    # Server
    host: str = "127.0.0.1"
    port: int = 8080

    model_config = {"env_prefix": "BILLMAP_", "env_file": ".env"}

    def resolve(self) -> "Settings":
        """Fill in defaults based on mode."""
        base = Path(__file__).resolve().parent.parent

        if not self.database_url:
            if self.mode == "local":
                db_path = base / "data" / "billmap.db"
                db_path.parent.mkdir(parents=True, exist_ok=True)
                self.database_url = f"sqlite:///{db_path}"
            else:
                self.database_url = "postgresql://billmap:billmap@localhost/billmap"

        if not self.upload_dir:
            upload_path = base / "uploads"
            upload_path.mkdir(parents=True, exist_ok=True)
            self.upload_dir = str(upload_path)

        return self


settings = Settings().resolve()
