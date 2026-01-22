from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Defines the application's settings, loaded from environment variables."""

    # PostgreSQL connection details.
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str
    POSTGRES_HOST: str
    POSTGRES_PORT: int

    @property
    def DATABASE_URL(self) -> str:
        """Constructs the asynchronous PostgreSQL database URL."""
        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    class Config:
        """Pydantic model configuration."""
        # Specifies the file to load environment variables from.
        env_file = ".env"


# Creates a global settings instance to be used throughout the application.
settings = Settings()
