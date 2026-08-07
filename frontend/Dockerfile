FROM python:3.11-slim

WORKDIR /app

# Frontend only needs streamlit + requests, not the backend's heavy ML stack
RUN pip install --no-cache-dir streamlit==1.40.1 requests==2.32.3

COPY frontend/ ./frontend/

EXPOSE 8501

CMD ["sh", "-c", "python -m streamlit run frontend/app.py --server.address=0.0.0.0 --server.port=${PORT:-8501}"]
