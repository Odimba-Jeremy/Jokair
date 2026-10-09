from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Dedikka API"
    app_env: str = "development"
    api_prefix: str = "/api/v1"
    database_url: str = "sqlite:///./dedicapag.db"
    supabase_url: str = ""
    supabase_secret_key: str = ""
    supabase_bucket: str = "dedikka-images"
    public_backend_url: str = ""
    upload_dir: str = "uploads"
    frontend_origins: list[str] = [
        "http://127.0.0.1:5500", "http://localhost:5500",
        "http://127.0.0.1:8000", "http://localhost:8000",
        "http://127.0.0.1:8001", "http://localhost:8001",
        "https://dedicapag.com", "https://dedikka.com", "https://dedikka.onrender.com",
    ]
    session_cookie_name: str = "dedicapag_session"
    csrf_cookie_name: str = "dedicapag_csrf"
    cookie_secure: bool = False
    session_days: int = 7
    rate_limit_login_attempts: int = 5
    rate_limit_window_seconds: int = 300

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
