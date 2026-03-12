from app.session_store import SessionStore
from app.source_store import SourceStore
from app.source_processor import SourceProcessor
from app.live_notebook_agent.sub_agents.retriever import Retriever


def main() -> None:
    session_store = SessionStore()
    source_store = SourceStore()
    source_processor = SourceProcessor()
    retriever = Retriever()

    metadata = session_store.create_session("index uploaded source test")

    source = source_store.add_uploaded_source(
        session_id=metadata.session_id,
        display_name="README.md",
        original_filename="README.md",
        mime_type="text/markdown",
        gcs_uri="gs://dummy/README.md",
    )

    with open("README.md", "rb") as f:
        content = f.read()

    chunks = source_processor.process_uploaded_bytes(
        source=source,
        filename="README.md",
        content=content,
    )
    print(f"Created {len(chunks)} chunks.")

    retriever.index_chunks_with_vertex_embeddings(
        session_id=metadata.session_id,
        chunks=chunks,
    )
    print("Indexed chunks into Pinecone.")

    source.processing_status = "indexed"
    source.chunk_count = len(chunks)
    source_store.update_source(source)

    results = retriever.retrieve_with_vertex_query(
        session_id=metadata.session_id,
        query="What does this project do?",
        top_k=3,
    )

    print("Query results:")
    for item in results:
        print(item)


if __name__ == "__main__":
    main()