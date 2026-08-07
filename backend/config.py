"""
Central configuration for the Enterprise RAG platform.
Loads settings from environment variables (.env file).
"""
import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    # Groq (LLM provider — powers the agentic RAG loop via tool calling)
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    # Agentic retrieval: max rounds of tool-initiated search per query
    # (guards against runaway search loops / cost)
    MAX_SEARCH_ROUNDS: int = int(os.getenv("MAX_SEARCH_ROUNDS", 4))

    # Auth: how long a login session stays valid before requiring re-login
    SESSION_EXPIRY_HOURS: int = int(os.getenv("SESSION_EXPIRY_HOURS", 12))

    # Embeddings (local, free, no API key needed)
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-base-en-v1.5")

    # Storage paths
    CHROMA_DB_PATH: str = os.getenv("CHROMA_DB_PATH", "./data/chroma_db")
    UPLOAD_PATH: str = os.getenv("UPLOAD_PATH", "./data/uploads")
    AUDIT_DB_PATH: str = os.getenv("AUDIT_DB_PATH", "./data/audit.db")

    # Chunking strategy
    CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", 1000))
    CHUNK_OVERLAP: int = int(os.getenv("CHUNK_OVERLAP", 150))

    # Retrieval
    TOP_K_RESULTS: int = int(os.getenv("TOP_K_RESULTS", 5))

    # Collection name in Chroma
    COLLECTION_NAME: str = "enterprise_knowledge_base"

settings = Settings()
