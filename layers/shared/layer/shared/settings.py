"""
Base settings. Each module subclasses this with its own fields.

Values come from environment variables (set in template.yaml), or from a
local .env file during development. A missing required value fails at cold
start with a clear message, instead of a KeyError in the middle of a request.

Only a module's container.py reads settings. Everything else receives plain
values through its constructor.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseAppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    event_bus_name: str = "default"
