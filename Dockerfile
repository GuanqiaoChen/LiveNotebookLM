FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY README.md ./
COPY app/ ./app/

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e .

# Run from app/ so imports like
# from live_notebook_agent.agent import root_agent
# work with your current structure.
WORKDIR /app/app

ENV PORT=8080
EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]