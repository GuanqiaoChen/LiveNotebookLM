import os
from google.cloud import storage


def _get_bucket():
    bucket_name = os.environ["GCS_BUCKET"]
    client = storage.Client()
    return client.bucket(bucket_name)


def upload_bytes(path: str, data: bytes, content_type: str) -> str:
    bucket = _get_bucket()
    blob = bucket.blob(path)
    blob.upload_from_string(data, content_type=content_type)
    return f"gs://{bucket.name}/{path}"


def upload_text(path: str, text: str) -> str:
    return upload_bytes(path, text.encode("utf-8"), "text/plain")