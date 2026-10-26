"""
The users' files: what they attach to messages, and what agents make for
them (reports, charts, data), in S3, one record each in the assistant table
(repository.FileRepository, service.FileService).

    <tenant>/<user>/<file_id>/<version>      a file's bytes, one object per version

An upload goes straight from the browser to S3, with a presigned POST the
HTTP API gives (its size and type are part of the signature), tagged
pending: the bucket's lifecycle rule deletes it a day later unless a message
takes it (the tag goes then). Downloads are presigned GETs too, so file
bytes never pass through a Lambda. This file is boto3 only: the HTTP API's
Lambda uses it.
"""
import mimetypes
from typing import Protocol
from urllib.parse import quote

from botocore.exceptions import ClientError

from assistant.models import FileKind

MAX_FILE_BYTES = 20 * 1024 * 1024    # an upload, or a file an agent saves
MAX_IMAGE_BYTES = 5 * 1024 * 1024    # an image the model is sent (the providers' common limit)
PENDING_TAG = "pending"              # template.yaml's lifecycle rule deletes objects tagged pending=true
URL_SECONDS = 900                    # a download or upload link

IMAGES = {"image/png", "image/jpeg", "image/gif", "image/webp"}   # what the models take
DATA = {"text/csv", "application/json", "text/tab-separated-values", "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
DOCUMENTS = {"application/pdf", "application/msword", "application/rtf",
             "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
             "application/vnd.openxmlformats-officedocument.presentationml.presentation"}
# Text an agent reads as it is: the model gets it inline.
TEXT_TYPES = {"application/xml", "application/x-yaml", "application/yaml", "application/javascript",
              "application/x-python", "application/x-sh", "application/sql", "image/svg+xml"}
TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".yaml", ".yml", ".xml", ".html", ".htm",
                 ".css", ".js", ".ts", ".tsx", ".jsx", ".py", ".sql", ".sh", ".log", ".ini", ".toml", ".svg"}
# Anything else that may be uploaded: an agent opens it in the sandbox (tools/files.py).
OTHER = {"application/zip", "application/octet-stream"}


def content_type_of(name: str, given: str | None = None) -> str:
    """What a file is, from what the browser said or, failing that, its name."""
    given = (given or "").split(";")[0].strip().lower()
    if given and given != "application/octet-stream":
        return given
    guessed, _ = mimetypes.guess_type(name)
    return guessed or ("text/plain" if _suffix(name) in TEXT_SUFFIXES else "application/octet-stream")


def is_text(name: str, content_type: str) -> bool:
    """Whether the file is text, read as it is (the model gets it inline)."""
    return content_type.startswith("text/") or content_type in TEXT_TYPES or content_type == "application/json" \
        or _suffix(name) in TEXT_SUFFIXES


def kind_of(name: str, content_type: str) -> FileKind:
    if content_type in IMAGES:
        return "image"
    if content_type in DATA:
        return "data"
    if content_type in DOCUMENTS:
        return "document"
    if is_text(name, content_type):
        return "text"
    return "other"


def accepted(name: str, content_type: str) -> bool:
    """What may be uploaded: images, text and code, data, documents, archives."""
    return content_type in IMAGES | DATA | DOCUMENTS | OTHER or is_text(name, content_type)


def clean_name(name: str) -> str:
    """A file's name without a folder, on one line, at most 200 characters, keeping its extension."""
    name = " ".join(name.replace("\\", "/").rsplit("/", 1)[-1].split()).strip(". ")
    if len(name) > 200:
        stem, dot, suffix = name.rpartition(".")
        name = stem[:200 - len(suffix) - 1] + dot + suffix if dot and len(suffix) <= 10 else name[:200]
    return name or "file"


def _suffix(name: str) -> str:
    return "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""


def object_key(tenant_id: str, user_id: str, file_id: str, version: int) -> str:
    return f"{tenant_id}/{user_id}/{file_id}/{version}"


class FileStore(Protocol):
    def upload_form(self, key: str, content_type: str, size: int) -> dict: ...
    def size(self, key: str) -> int | None: ...
    def keep(self, key: str) -> None: ...
    def read(self, key: str) -> bytes: ...
    def write(self, key: str, data: bytes, content_type: str) -> None: ...
    def delete(self, keys: list[str]) -> None: ...
    def delete_prefix(self, prefix: str) -> int: ...
    def link(self, key: str, name: str, content_type: str, inline: bool = False) -> str: ...


class S3FileStore:
    def __init__(self, client, bucket: str):
        self.client = client   # boto3 "s3", with the bucket's region (presigned links are signed for it)
        self.bucket = bucket

    def upload_form(self, key: str, content_type: str, size: int) -> dict:
        """
        A presigned POST: {"url", "fields"} for a multipart form whose last
        field is the file. Only this key, this type, exactly `size` bytes,
        and tagged pending (expires unless a message takes it).
        """
        tagging = f"<Tagging><TagSet><Tag><Key>{PENDING_TAG}</Key><Value>true</Value></Tag></TagSet></Tagging>"
        return self.client.generate_presigned_post(
            Bucket=self.bucket, Key=key, ExpiresIn=URL_SECONDS,
            Fields={"Content-Type": content_type, "tagging": tagging},
            Conditions=[{"Content-Type": content_type}, {"tagging": tagging}, ["content-length-range", size, size]],
        )

    def size(self, key: str) -> int | None:
        """The object's size, or None if it isn't there (not uploaded yet)."""
        try:
            return self.client.head_object(Bucket=self.bucket, Key=key)["ContentLength"]
        except ClientError as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return None
            raise

    def keep(self, key: str) -> None:
        """No longer pending: the lifecycle rule leaves it alone."""
        self.client.delete_object_tagging(Bucket=self.bucket, Key=key)

    def read(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def write(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def delete(self, keys: list[str]) -> None:
        for start in range(0, len(keys), 1000):   # DeleteObjects takes 1,000 at a time
            self.client.delete_objects(Bucket=self.bucket,
                                       Delete={"Objects": [{"Key": key} for key in keys[start:start + 1000]]})

    def delete_prefix(self, prefix: str) -> int:
        """Every object under `prefix`; returns how many."""
        deleted = 0
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            keys = [item["Key"] for item in page.get("Contents", [])]
            self.delete(keys)
            deleted += len(keys)
        return deleted

    def link(self, key: str, name: str, content_type: str, inline: bool = False) -> str:
        """
        A presigned GET, for URL_SECONDS: a download as `name`, or, `inline`,
        to show (images only: anything else opens as a download, so a file
        an agent wrote never runs as a page).
        """
        disposition = "inline" if inline else f"attachment; filename*=UTF-8''{quote(name)}"
        return self.client.generate_presigned_url(
            "get_object", ExpiresIn=URL_SECONDS,
            Params={"Bucket": self.bucket, "Key": key, "ResponseContentDisposition": disposition,
                    "ResponseContentType": content_type if inline else "application/octet-stream"})
