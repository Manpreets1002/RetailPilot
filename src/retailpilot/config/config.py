from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """All configuration for RetailPilot, read from the environment and the repo-root .env file.

    Credentials default to None. Everything else has a local-development default.
    """

    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    google_api_key: str | None = None
    django_secret_key: str | None = None

    gemini_model: str = "gemini-2.5-flash"
    mongo_uri: str = "mongodb://localhost:27018"
    mongo_db: str = "amazon_ecomm"
    chat_db: str = "chatbot"
    backend_url: str = "http://127.0.0.1:8000"
    stale_hours: float = 48.0

    django_debug: bool = True
    django_allowed_hosts: str = "localhost,127.0.0.1"

    base_url: str = Field("https://www.amazon.in", validation_alias="AMAZON_BASE_URL")
    search_index: str = Field("electronics", validation_alias="AMAZON_SEARCH_INDEX")
    root_node: str = Field("976419031", validation_alias="AMAZON_ROOT_NODE")
    min_delay: float = Field(2.0, validation_alias="CRAWL_MIN_DELAY")
    max_delay: float = Field(5.0, validation_alias="CRAWL_MAX_DELAY")
    max_retries: int = Field(3, validation_alias="CRAWL_MAX_RETRIES")
    timeout: int = Field(30, validation_alias="CRAWL_TIMEOUT")
    impersonate: str = Field("chrome", validation_alias="CRAWL_IMPERSONATE")
    max_pages_per_category: int = Field(2, validation_alias="CRAWL_MAX_PAGES")
    max_products_per_category: int = Field(20, validation_alias="CRAWL_MAX_PRODUCTS")
    max_reviews_per_product: int = Field(10, validation_alias="CRAWL_MAX_REVIEWS")
    accept_language: str = "en-IN,en;q=0.9"
    extra_headers: dict[str, str] = Field(default_factory=dict)

    @property
    def cities_file(self) -> Path:
        return REPO_ROOT / "amazon_ecomm_crawler" / "cities.json"


settings = Settings()
