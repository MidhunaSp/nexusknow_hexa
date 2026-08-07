FROM python:3.11-slim

# System deps needed by `unstructured` (pdf/docx/pptx parsing) and PyMuPDF
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmagic1 \
    poppler-utils \
    tesseract-ocr \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/

# Persisted at runtime via Docker volumes (see docker-compose.yml)
RUN mkdir -p /app/data/uploads /app/data/chroma_db

EXPOSE 8000

CMD ["sh", "-c", "python -m uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
