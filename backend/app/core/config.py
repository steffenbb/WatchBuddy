
import os
from pydantic import BaseSettings



class Settings(BaseSettings):
    db_user: str = os.getenv("POSTGRES_USER", "watchbuddy")
    db_password: str = os.getenv("POSTGRES_PASSWORD", "watchbuddy")
    db_name: str = os.getenv("POSTGRES_DB", "watchbuddy")
    database_url: str = f"postgresql+psycopg2://{os.getenv('POSTGRES_USER', 'watchbuddy')}:{os.getenv('POSTGRES_PASSWORD', 'watchbuddy')}@db:5432/{os.getenv('POSTGRES_DB', 'watchbuddy')}"
    redis_url: str = os.getenv("REDIS_URL", "redis://redis:6379/0")
    trakt_redirect_uri: str = "http://localhost:5173/auth/callback"

settings = Settings()
