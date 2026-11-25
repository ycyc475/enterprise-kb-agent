from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    postgres_host: str = "127.0.0.1"
    postgres_port: int = 55432
    postgres_db: str = "enterprise_kb"
    postgres_user: str = "kb_bootstrap"
    postgres_password: SecretStr
    postgres_app_user: str = "kb_app"
    postgres_app_password: SecretStr
    jwt_secret_key: SecretStr
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    app_origins: str = (
        "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:8000,http://localhost:8000"
    )
    cookie_secure: bool = False
    refresh_cookie: str = "kb_refresh"
    jwt_issuer: str = "enterprise-kb-agent"
    jwt_audience: str = "enterprise-kb-web"
    dashscope_api_key: SecretStr = SecretStr("")
    dashscope_base_url: str = ""
    chat_model: str = ""
    embedding_model: str = "text-embedding-v4"
    embedding_dimensions: int = 1024
    embedding_revision: str = "v1"
    storage_dir: Path = ROOT / "data/runtime/files"
    worker_enabled: bool = True
    max_file_bytes: int = 10_000_000
    max_pdf_pages: int = 100
    max_extracted_chars: int = 100_000
    max_current_documents: int = 30
    budget_warning_cny: Decimal = Decimal("80")
    budget_limit_cny: Decimal = Decimal("100")
    max_search_attempts: int = Field(default=5, ge=1, le=20)
    max_model_attempts: int = Field(default=10, ge=3, le=30)
    max_run_seconds: int = Field(default=180, ge=30, le=600)
    chat_max_output_tokens: int = 1800

    @model_validator(mode="after")
    def validate_security(self):
        if len(self.jwt_secret_key.get_secret_value()) < 32:
            raise ValueError("JWT密钥至少需要32个字符，请先运行初始化脚本")
        if self.jwt_algorithm != "HS256":
            raise ValueError("当前签名算法只允许HS256")
        if not 1 <= self.access_token_minutes <= 60 or not 1 <= self.refresh_token_days <= 30:
            raise ValueError("令牌有效期超出允许范围")
        if self.embedding_model != "text-embedding-v4" or self.embedding_dimensions != 1024:
            raise ValueError(
                "M2索引固定为text-embedding-v4/1024，更换模型或维度需要显式迁移和重建索引"
            )
        if not 0 < self.budget_warning_cny <= self.budget_limit_cny:
            raise ValueError("预算阈值不合法")
        if not 256 <= self.chat_max_output_tokens <= 3000:
            raise ValueError("对话输出上限应在256至3000 Token之间")
        return self

    @property
    def origins(self) -> list[str]:
        return [item.strip().rstrip("/") for item in self.app_origins.split(",") if item.strip()]

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_app_user,
            password=self.postgres_app_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
