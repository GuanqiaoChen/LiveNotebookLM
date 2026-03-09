import os
from pathlib import Path

from dotenv import load_dotenv
from google import genai

# 显式加载 app/.env
load_dotenv(Path(__file__).resolve().parent / ".env")

required = [
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "GOOGLE_GENAI_USE_VERTEXAI",
    "LIVE_NOTEBOOK_AGENT_MODEL",
]

missing = [k for k in required if not os.getenv(k)]
if missing:
    raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

if os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").lower() != "true":
    raise RuntimeError("GOOGLE_GENAI_USE_VERTEXAI must be set to true")

test_model = "gemini-2.5-flash"

print("Using Vertex AI config:")
print(f"  project = {os.environ['GOOGLE_CLOUD_PROJECT']}")
print(f"  location = {os.environ['GOOGLE_CLOUD_LOCATION']}")
print(f"  model = {test_model}")

client = genai.Client()

response = client.models.generate_content(
    model=test_model,
    contents="Reply with exactly: Vertex AI smoke test passed."
)

print("\nModel response:")
print(response.text)