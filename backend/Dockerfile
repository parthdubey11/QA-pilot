FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
# Bake the RAG embedding model (all-MiniLM-L6-v2, ~80 MB) into the image instead of downloading it at runtime.
RUN python -c "from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2 as M; M(preferred_providers=['CPUExecutionProvider'])(['warm up'])"

COPY . .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
