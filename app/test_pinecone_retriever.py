from app.live_notebook_agent.sub_agents.retriever import Retriever


def main() -> None:
    retriever = Retriever()
    if not retriever.is_configured():
        raise RuntimeError("Retriever is not configured with Pinecone")

    session_id = "pinecone-test-session"

    chunks = [
        {
            "chunk_id": "chunk-1",
            "session_id": session_id,
            "source_id": "source-1",
            "source_name": "test-source",
            "text": "Transformer models are useful for sequence learning.",
            "page": 1,
            "section": "intro",
        },
        {
            "chunk_id": "chunk-2",
            "session_id": session_id,
            "source_id": "source-1",
            "source_name": "test-source",
            "text": "Voice interfaces need low-latency streaming interaction.",
            "page": 2,
            "section": "discussion",
        },
        {
            "chunk_id": "chunk-3",
            "session_id": session_id,
            "source_id": "source-1",
            "source_name": "test-source",
            "text": "Pinecone is used here as the vector database for retrieval.",
            "page": 3,
            "section": "infra",
        },
    ]

    retriever.index_chunks_with_vertex_embeddings(session_id, chunks)
    print("Upsert with Vertex embeddings complete.")

    results = retriever.retrieve_with_vertex_query(
        session_id=session_id,
        query="How do I build a voice-first real-time agent?",
        top_k=3,
    )

    print("Query results:")
    for item in results:
        print(item)


if __name__ == "__main__":
    main()