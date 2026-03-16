from __future__ import annotations

from typing import Optional

from pinecone import Pinecone

from app.config import get_settings
from app.embedding_service import EmbeddingService
from app.source_processor import SourceProcessor


class Retriever:
    """
    Vector-search retriever backed by Pinecone.

    Falls back to local keyword search when Pinecone is not configured.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self.source_processor = SourceProcessor()
        self.embedding_service = EmbeddingService()

        self.pc: Optional[Pinecone] = None
        self.index = None

        if self.settings.pinecone_api_key and self.settings.pinecone_index_name:
            self.pc = Pinecone(api_key=self.settings.pinecone_api_key)
            self.index = self.pc.Index(self.settings.pinecone_index_name)

    # ── Public API ───────────────────────────────────────────────────────────

    def namespace_for_session(self, session_id: str) -> str:
        return f"{self.settings.pinecone_namespace_prefix}:{session_id}"

    def is_configured(self) -> bool:
        return self.index is not None

    def upsert_chunks(
        self,
        session_id: str,
        chunks: list[dict],
        embeddings: list[list[float]],
    ) -> None:
        if self.index is None:
            raise RuntimeError("Pinecone is not configured. Missing API key or index name.")

        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings must have the same length")

        vectors = []
        for chunk, embedding in zip(chunks, embeddings):
            metadata = {
                "session_id": chunk["session_id"],
                "source_id": chunk["source_id"],
                "source_name": chunk["source_name"],
                "text": chunk["text"],
            }

            # Pinecone metadata cannot have None values
            if chunk.get("page") is not None:
                metadata["page"] = chunk["page"]

            if chunk.get("section") is not None:
                metadata["section"] = chunk["section"]

            vectors.append(
                {
                    "id": chunk["chunk_id"],
                    "values": embedding,
                    "metadata": metadata,
                }
            )

        self.index.upsert(
            vectors=vectors,
            namespace=self.namespace_for_session(session_id),
        )

    def index_chunks_with_vertex_embeddings(
        self,
        session_id: str,
        chunks: list[dict],
        batch_size: int = 100,
        output_dimensionality: int = 3072,
    ) -> None:
        if self.index is None:
            raise RuntimeError("Pinecone is not configured. Missing API key or index name.")

        if not chunks:
            return

        for batch_start in range(0, len(chunks), batch_size):
            batch_chunks = chunks[batch_start: batch_start + batch_size]
            texts = [chunk["text"] for chunk in batch_chunks]
            embeddings = self.embedding_service.embed_documents(
                texts,
                output_dimensionality=output_dimensionality,
            )
            self.upsert_chunks(session_id, batch_chunks, embeddings)

    def retrieve(
        self,
        session_id: str,
        query_embedding: list[float],
        top_k: int = 5,
    ) -> list[dict]:
        if self.index is None:
            raise RuntimeError("Pinecone is not configured. Missing API key or index name.")

        result = self.index.query(
            namespace=self.namespace_for_session(session_id),
            vector=query_embedding,
            top_k=top_k,
            include_metadata=True,
        )

        matches = getattr(result, "matches", []) or []
        evidence: list[dict] = []

        for match in matches:
            metadata = match.metadata or {}
            evidence.append(
                {
                    "chunk_id": match.id,
                    "score": float(match.score),
                    "source_id": metadata.get("source_id"),
                    "source_name": metadata.get("source_name"),
                    "text": metadata.get("text"),
                    "page": metadata.get("page"),
                    "section": metadata.get("section"),
                }
            )

        return evidence

    def retrieve_with_vertex_query(
        self,
        session_id: str,
        query: str,
        top_k: int = 5,
        output_dimensionality: int = 3072,
    ) -> list[dict]:
        query_embedding = self.embedding_service.embed_query(
            query,
            output_dimensionality=output_dimensionality,
        )
        return self.retrieve(
            session_id=session_id,
            query_embedding=query_embedding,
            top_k=top_k,
        )

    def retrieve_local_fallback(
        self,
        session_id: str,
        source_ids: list[str],
        query: str,
        top_k: int = 5,
    ) -> list[dict]:
        query_terms = [term.lower() for term in query.split() if term.strip()]
        all_chunks: list[dict] = []

        for source_id in source_ids:
            all_chunks.extend(self.source_processor.get_chunks(session_id, source_id))

        scored: list[dict] = []
        for chunk in all_chunks:
            text = (chunk.get("text") or "").lower()
            score = sum(text.count(term) for term in query_terms)
            if score > 0:
                scored.append(
                    {
                        "chunk_id": chunk["chunk_id"],
                        "score": float(score),
                        "source_id": chunk["source_id"],
                        "source_name": chunk["source_name"],
                        "text": chunk["text"],
                        "page": chunk.get("page"),
                        "section": chunk.get("section"),
                    }
                )

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]