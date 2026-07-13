"""Create a private S3-compatible client for AWS S3, R2, or MinIO."""

from __future__ import annotations

from typing import Any

import boto3
from botocore.config import Config


def create_s3_client(
    *,
    endpoint_url: str | None,
    region: str,
    access_key_id: str,
    secret_access_key: str,
    path_style: bool,
) -> Any:
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url or None,
        region_name=region,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path" if path_style else "virtual"},
        ),
    )
