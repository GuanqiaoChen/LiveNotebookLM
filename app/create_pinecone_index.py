from pinecone import Pinecone, ServerlessSpec
from app.config import get_settings


def main() -> None:
    settings = get_settings()

    if not settings.pinecone_api_key:
        raise RuntimeError("Missing PINECONE_API_KEY")
    if not settings.pinecone_index_name:
        raise RuntimeError("Missing PINECONE_INDEX_NAME")

    pc = Pinecone(api_key=settings.pinecone_api_key)
    index_name = settings.pinecone_index_name

    existing = [idx["name"] for idx in pc.list_indexes()]
    if index_name in existing:
        print(f"Index already exists: {index_name}")
        return

    pc.create_index(
        name=index_name,
        dimension=3072,
        metric="cosine",
        spec=ServerlessSpec(
            cloud="aws",
            region="us-east-1",
        ),
        deletion_protection="disabled",
    )
    print(f"Created index: {index_name}")


if __name__ == "__main__":
    main()