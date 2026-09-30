"""
Cloudflare R2 Storage Service
-----------------------------------
Thin wrapper around boto3's S3 client, pointed at R2's S3-compatible
endpoint. R2 has zero egress fees (unlike S3), which matters once
hospitals are regularly downloading patient documents.

Signed URLs:
  Generated documents contain PHI (patient names, diagnoses, invoice
  amounts). The R2 bucket is NEVER public. Every download goes through
  a time-limited presigned URL (default 1 hour), generated on-demand
  when a document is requested — never a permanent public link.
"""

import boto3
from botocore.config import Config as BotoConfig

from app.config import settings


def _get_r2_client():
    endpoint_url = f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        config=BotoConfig(signature_version="s3v4"),
        region_name="auto",
    )


def upload_pdf(storage_key: str, pdf_bytes: bytes) -> int:
    """
    Uploads PDF bytes to R2 under the given key.
    Returns the byte size uploaded (for logging in GeneratedDocument).
    """
    client = _get_r2_client()
    client.put_object(
        Bucket=settings.R2_BUCKET_NAME,
        Key=storage_key,
        Body=pdf_bytes,
        ContentType="application/pdf",
    )
    return len(pdf_bytes)


def get_signed_download_url(storage_key: str, expires_in_seconds: int = 3600) -> str:
    """Generates a time-limited presigned URL for downloading a private object."""
    client = _get_r2_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.R2_BUCKET_NAME, "Key": storage_key},
        ExpiresIn=expires_in_seconds,
    )


def build_storage_key(hospital_id: str, document_type: str, document_id: str) -> str:
    """
    Consistent key structure: hospital_id/document_type/document_id.pdf
    Namespacing by hospital_id means even if a key were ever guessed,
    it's still scoped per-hospital in the bucket's logical structure.
    """
    return f"{hospital_id}/{document_type}/{document_id}.pdf"