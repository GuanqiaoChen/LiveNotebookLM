# LiveNotebookLM - Cloud Run container
# Python 3.10+ required for google-adk

FROM python:3.12-slim

WORKDIR /app

# Copy project and install dependencies
COPY pyproject.toml ./
COPY app/ ./app/
RUN pip install --no-cache-dir -e .

# Run from app/ so Python finds live_notebook_agent
WORKDIR /app/app

ENV PORT=8080
EXPOSE 8080

# Cloud Run expects to bind to PORT
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT}
