from functools import lru_cache
from typing import Literal
from pydantic import SecretStr, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from cryptography.fernet import Fernet

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    environment: Literal["production","test"] = "production"
    database_url: str
    encryption_key: SecretStr
    public_base_url: str = 'https://origin.xdoublegroup.online:8443'
    drive_credentials_file: str = '/run/secrets/drive.json'
    drive_auth_type: str = 'oauth'
    telegram_bot_token: SecretStr = SecretStr('')
    telegram_admin_ids: list[int] = []
    telegram_allow_all: bool = False
    lease_seconds: int = Field(180, ge=30, le=900)
    max_processing_seconds: int = Field(1800, ge=60, le=7200)
    heartbeat_timeout: int = Field(120, ge=30)
    rate_limit_per_minute: int = Field(120, ge=1)
    @model_validator(mode='after')
    def validate_config(self):
        Fernet(self.encryption_key.get_secret_value().encode())
        if not self.database_url.startswith('postgresql+psycopg://'):
            raise ValueError('PostgreSQL with psycopg is required')
        if self.drive_auth_type not in ('oauth','service_account'):
            raise ValueError('drive_auth_type must be oauth or service_account')
        if not self.public_base_url.startswith('https://'):
            raise ValueError('HTTPS public URL required')
        return self

@lru_cache
def settings():
    return Settings()
