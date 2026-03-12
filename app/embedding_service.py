from __future__ import annotations

from typing import Iterable

from google import genai

from app.config import get_settings


class EmbeddingService:
    def __init__(self) -> None:
        settings = get_settings()
        self.client = genai.Client(
            vertexai=True,
            project=settings.google_cloud_project,
            location=settings.google_cloud_location,
        )
        self.model_name = "gemini-embedding-001"

    def embed_documents(
        self,
        texts: list[str],
        output_dimensionality: int = 3072,
    ) -> list[list[float]]:
        if not texts:
            return []

        response = self.client.models.embed_content(
            model=self.model_name,
            contents=texts,
            config={
                "task_type": "RETRIEVAL_DOCUMENT",
                "output_dimensionality": output_dimensionality,
            },
        )

        return [item.values for item in response.embeddings]

    def embed_query(
        self,
        text: str,
        output_dimensionality: int = 3072,
    ) -> list[float]:
        response = self.client.models.embed_content(
            model=self.model_name,
            contents=[text],
            config={
                "task_type": "RETRIEVAL_QUERY",
                "output_dimensionality": output_dimensionality,
            },
        )
        return response.embeddings[0].values

    def embed_single_document(
        self,
        text: str,
        output_dimensionality: int = 3072,
    ) -> list[float]:
        return self.embed_documents([text], output_dimensionality=output_dimensionality)[0]

    @staticmethod
    def batch(iterable: list[str], batch_size: int = 100) -> Iterable[list[str]]:
        for i in range(0, len(iterable), batch_size):
            yield iterable[i:i + batch_size]