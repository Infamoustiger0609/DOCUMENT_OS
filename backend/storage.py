from supabase import Client, create_client

from config import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL

BUCKET_NAME = "DOCUMENT OS AI"

client: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def upload_file_to_storage(file_bytes: bytes, key: str, content_type: str) -> str:
    client.storage.from_(BUCKET_NAME).upload(
        path=key,
        file=file_bytes,
        file_options={"content-type": content_type},
    )
    return key


def download_file_from_storage(key: str) -> bytes:
    return client.storage.from_(BUCKET_NAME).download(key)


def delete_file_from_storage(key: str) -> None:
    client.storage.from_(BUCKET_NAME).remove([key])


def create_signed_url(key: str, expires_in: int) -> str:
    """The 'documents' bucket is private, so viewing a file client-side needs a
    short-lived signed URL rather than a public link. Used by GET /documents/{id}/download-url."""
    response = client.storage.from_(BUCKET_NAME).create_signed_url(key, expires_in)
    signed_url = response.get("signedURL")
    if not signed_url:
        raise RuntimeError("Supabase Storage did not return a signed URL.")
    return signed_url
