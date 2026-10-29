"""
Exports: a copy of a workspace's or a user's data, as JSON files a user downloads.

A job a user starts and watches (the pattern for any long job): accounts saves
the job (JOB# item: the parts it waits for, and those done) and publishes
ExportRequested in one transaction. Every module that keeps data for it writes
its own part to the exports bucket, with its own role:

  exports/<kind>/<owner_id>/<job_id>/<module>.json   (kind: workspace or user;
                                                       owner_id: the tenant or the user)

The bucket tells EventBridge about each new object, and accounts marks that part
done; when all are, the job is done and ExportReady tells the requester. The
bucket removes exports after 7 days, and TTL the job.

  ExportRequested: tenant_id ("" for a user's export), job_id, kind, owner_id,
                   user_id (who asked), email, tenant_ids (a user export: their workspaces)
"""
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

EXPORT_REQUESTED = "ExportRequested"
EXPORT_READY = "ExportReady"
KINDS = ("workspace", "user")
KEY_FIELDS = ("pk", "sk", "gsi1pk", "gsi1sk", "expires_at")


def part_key(kind: str, owner_id: str, job_id: str, part: str) -> str:
    return f"exports/{kind}/{owner_id}/{job_id}/{part}.json"


def parse_key(key: str) -> tuple[str, str, str, str] | None:
    """(kind, owner_id, job_id, part) of an export's object key, or None."""
    pieces = key.split("/")
    if len(pieces) != 5 or pieces[0] != "exports" or pieces[1] not in KINDS or not pieces[4].endswith(".json"):
        return None
    return pieces[1], pieces[2], pieces[3], pieces[4].removesuffix(".json")


def clean(item: dict) -> dict:
    """An item as stored, without its keys: what an export shows."""
    return {k: v for k, v in item.items() if k not in KEY_FIELDS}


def _plain(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, set):
        return sorted(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)


def write_part(s3, bucket: str, detail: dict, part: str, data) -> None:
    """Writes this module's part of the export the event asks for. Writing it again replaces it."""
    s3.put_object(
        Bucket=bucket, Key=part_key(detail["kind"], detail["owner_id"], detail["job_id"], part),
        Body=json.dumps(data, default=_plain, indent=2, ensure_ascii=False).encode(),
        ContentType="application/json", ServerSideEncryption="AES256",
    )


class PartWriter(Protocol):
    def write(self, detail: dict, part: str, data) -> None:
        """Writes this module's part of the export ExportRequested `detail` asks for."""
        ...


class S3PartWriter:
    """The real PartWriter: the exports bucket (EXPORTS_BUCKET), with the module's own role."""

    def __init__(self, client, bucket: str | None):
        self.client = client
        self.bucket = bucket

    def write(self, detail: dict, part: str, data) -> None:
        if not self.bucket:
            raise RuntimeError("EXPORTS_BUCKET is not configured for this function")
        write_part(self.client, self.bucket, detail, part, data)


class FakePartWriter:
    """For tests: keeps what each part says, by key."""

    def __init__(self):
        self.written: dict[str, object] = {}

    def write(self, detail: dict, part: str, data) -> None:
        self.written[part_key(detail["kind"], detail["owner_id"], detail["job_id"], part)] = data
