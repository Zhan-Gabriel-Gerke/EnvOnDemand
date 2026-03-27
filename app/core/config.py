from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Defines the application's settings, loaded from environment variables."""

    # PostgreSQL connection details.
    DATABASE_URL: str

    # JWT settings.
    # IMPORTANT: Set a strong, random SECRET_KEY in your .env file.
    SECRET_KEY: str = "CHANGE_ME_IN_PRODUCTION_USE_A_LONG_RANDOM_STRING"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    class Config:
        """Pydantic model configuration."""
        # Specifies the file to load environment variables from.
        env_file = ".env"
        extra = "ignore"


# Creates a global settings instance to be used throughout the application.
settings = Settings()
