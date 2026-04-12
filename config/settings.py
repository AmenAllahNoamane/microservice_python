from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    GROQ_API_KEY: str
    GEMINI_API_KEY: str
    PORT: int = 5001
    MAX_FILE_SIZE_MB: int = 20
    OCR_MIN_WORDS: int = 20

    class Config:
        env_file = ".env"

settings = Settings()