FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models \
    FASTEMBED_CACHE_PATH=/models

COPY pyproject.toml requirements.txt ./
COPY src ./src
COPY sql ./sql
COPY data ./data
RUN pip install -r requirements.txt

RUN useradd --create-home app && mkdir -p /models && chown app /models
USER app

EXPOSE 8000
CMD ["uvicorn", "kb.web.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
