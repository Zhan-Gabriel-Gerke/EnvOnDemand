"""
tests/test_config.py
Coverage tests for app.core.config
"""
import pytest
from pathlib import Path
from unittest.mock import patch


def test_settings_loads_from_env():
    """Settings instance reads DATABASE_URL from the real .env."""
    from app.core.config import settings
    assert settings.DATABASE_URL is not None
    assert isinstance(settings.DATABASE_URL, str)


def test_settings_default_secret_key():
    """SECRET_KEY has a default value when not overridden."""
    from app.core.config import Settings
    with patch.dict("os.environ", {"DATABASE_URL": "postgresql+asyncpg://u:p@localhost/db"}, clear=False):
        s = Settings()
    assert s.SECRET_KEY is not None


def test_settings_default_token_expire():
    """ACCESS_TOKEN_EXPIRE_MINUTES defaults to 60."""
    from app.core.config import Settings
    with patch.dict("os.environ", {"DATABASE_URL": "postgresql+asyncpg://u:p@localhost/db"}, clear=False):
        s = Settings()
    assert s.ACCESS_TOKEN_EXPIRE_MINUTES == 60


def test_settings_custom_values():
    """Settings accepts overridden values via env vars."""
    from app.core.config import Settings
    env_overrides = {
        "DATABASE_URL": "postgresql+asyncpg://user:pass@host/mydb",
        "SECRET_KEY": "my_custom_secret",
        "ACCESS_TOKEN_EXPIRE_MINUTES": "120",
    }
    with patch.dict("os.environ", env_overrides, clear=False):
        s = Settings()
    assert s.DATABASE_URL == "postgresql+asyncpg://user:pass@host/mydb"
    assert s.SECRET_KEY == "my_custom_secret"
    assert s.ACCESS_TOKEN_EXPIRE_MINUTES == 120


def test_base_dir_is_path():
    """BASE_DIR is a Path pointing to the project root."""
    from app.core import config
    assert isinstance(config.BASE_DIR, Path)


def test_env_file_path_points_to_dot_env():
    """ENV_FILE_PATH ends with .env."""
    from app.core import config
    assert config.ENV_FILE_PATH.name == ".env"


def test_env_file_path_is_child_of_base_dir():
    """ENV_FILE_PATH is directly inside BASE_DIR."""
    from app.core import config
    assert config.ENV_FILE_PATH.parent == config.BASE_DIR


def test_settings_extra_fields_ignored():
    """Extra env vars are silently ignored (extra='ignore')."""
    from app.core.config import Settings
    env_overrides = {
        "DATABASE_URL": "postgresql+asyncpg://u:p@localhost/db",
        "TOTALLY_UNKNOWN_KEY": "should_be_ignored",
    }
    with patch.dict("os.environ", env_overrides, clear=False):
        s = Settings()
    assert not hasattr(s, "TOTALLY_UNKNOWN_KEY")


def test_settings_singleton_is_settings_instance():
    """The module-level `settings` object is an instance of Settings."""
    from app.core.config import settings, Settings
    assert isinstance(settings, Settings)
