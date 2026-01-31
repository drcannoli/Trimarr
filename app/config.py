import os
from functools import lru_cache


@lru_cache
def get_settings():
    return Settings()


class Settings:
    def __init__(self):
        self.sonarr_url = os.getenv("SONARR_URL", "http://localhost:8989").rstrip("/")
        self.sonarr_api_key = os.getenv("SONARR_API_KEY", "")
        self.dry_run = os.getenv("TRIMARR_DRY_RUN", "true").lower() in ("1", "true", "yes")
        try:
            self.run_interval_hours = float(os.getenv("TRIMARR_INTERVAL", "0"))
        except ValueError:
            self.run_interval_hours = 0.0
