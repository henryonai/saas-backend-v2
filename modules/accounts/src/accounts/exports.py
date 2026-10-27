"""
Where exports are written and read (shared/exports.py has the pattern): the
exports bucket, behind a small interface so tests can use a fake.
"""
from typing import Protocol

from shared.exports import part_key, write_part

LINK_SECONDS = 15 * 60   # a download link works this long


class ExportStore(Protocol):
    def write(self, detail: dict, part: str, data) -> None:
        """Writes accounts' part of the export ExportRequested `detail` asks for."""
        ...

    def link(self, kind: str, owner_id: str, job_id: str, part: str) -> str:
        """A short-lived download link to one part."""
        ...


class S3ExportStore:
    def __init__(self, client, bucket: str | None):
        self.client = client
        self.bucket = bucket

    def write(self, detail: dict, part: str, data) -> None:
        write_part(self.client, self._bucket(), detail, part, data)

    def link(self, kind: str, owner_id: str, job_id: str, part: str) -> str:
        return self.client.generate_presigned_url("get_object", ExpiresIn=LINK_SECONDS, Params={
            "Bucket": self._bucket(), "Key": part_key(kind, owner_id, job_id, part),
            "ResponseContentDisposition": f'attachment; filename="{kind}-export-{part}.json"',
        })

    def _bucket(self) -> str:
        if not self.bucket:
            raise RuntimeError("EXPORTS_BUCKET is not configured for this function")
        return self.bucket
