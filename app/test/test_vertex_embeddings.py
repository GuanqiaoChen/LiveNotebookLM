from app.embedding_service import EmbeddingService


def main() -> None:
    service = EmbeddingService()

    docs = [
        "Transformer models are useful for sequence learning.",
        "Voice interfaces need low-latency streaming interaction.",
    ]

    doc_embeddings = service.embed_documents(docs)
    print(f"Generated {len(doc_embeddings)} document embeddings.")
    print(f"Embedding dimension: {len(doc_embeddings[0])}")

    query_embedding = service.embed_query("How does a real-time voice agent work?")
    print(f"Query embedding dimension: {len(query_embedding)}")


if __name__ == "__main__":
    main()