"""The server's store on real S3, through boto3. The same seven calls as cloud/store_fake.py, plus the database
backup.

Pieces never pass through here: presign_part signs an UploadPart link with the piece's SHA-256 as a signed header,
so S3 itself refuses a piece whose bytes differ. Since botocore 1.36 a client adds its own CRC32 checksum to
uploads by default; request_checksum_calculation="when_required" and passing only ChecksumSHA256 (never
ChecksumAlgorithm) keep the link's signed headers to host and x-amz-checksum-sha256, which is all the uploader sends.
"""
from concurrent.futures import ThreadPoolExecutor

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from api import BadUpload, NoSuchUpload

BACKUP_KEY = "backups/perception.db"
REFUSED_AT_COMPLETE = {"InvalidPart", "InvalidPartOrder", "EntityTooSmall", "InvalidRequest"}


def iso(t) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def make_client(region: str):
    return boto3.client("s3", region_name=region, endpoint_url=f"https://s3.{region}.amazonaws.com",
                        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"},
                                      request_checksum_calculation="when_required",
                                      response_checksum_validation="when_required"))


class S3Store:
    def __init__(self, bucket: str, region: str, client=None):
        self.bucket = bucket
        self.s3 = client or make_client(region)

    def _call(self, op: str, **params):
        try:
            return getattr(self.s3, op)(Bucket=self.bucket, **params)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code == "NoSuchUpload":
                raise NoSuchUpload() from e
            if op == "complete_multipart_upload" and code in REFUSED_AT_COMPLETE:
                raise BadUpload(f"S3 refused to join the parts: {e.response['Error'].get('Message', code)}") from e
            raise

    def head(self, key: str) -> dict | None:
        # A HEAD on a missing key is 403, not 404, unless the role may list the whole bucket, and the role may list
        # only labs/. A one-key listing tells "missing" apart from "not allowed".
        found = self._call("list_objects_v2", Prefix=key, MaxKeys=1).get("Contents", [])
        if not found or found[0]["Key"] != key:
            return None
        h = self._call("head_object", Key=key)
        meta = h.get("Metadata", {})
        return {"size": h["ContentLength"], "sha256": meta.get("sha256", ""), "kind": meta.get("kind", ""),
                "modified": iso(h["LastModified"])}

    def create(self, key: str, content_type: str, metadata: dict) -> str:
        return self._call("create_multipart_upload", Key=key, ContentType=content_type, Metadata=metadata,
                          ChecksumAlgorithm="SHA256")["UploadId"]

    def presign_part(self, key: str, upload_id: str, n: int, sha256_b64: str, expires_s: int) -> tuple[str, dict]:
        url = self.s3.generate_presigned_url(
            "upload_part", ExpiresIn=expires_s,
            Params={"Bucket": self.bucket, "Key": key, "UploadId": upload_id, "PartNumber": n, "ChecksumSHA256": sha256_b64})
        return url, {"x-amz-checksum-sha256": sha256_b64}

    def list_parts(self, key: str, upload_id: str) -> list[dict]:
        out, marker = [], 0
        while True:
            r = self._call("list_parts", Key=key, UploadId=upload_id, PartNumberMarker=marker)
            out += [{"n": p["PartNumber"], "size": p["Size"], "etag": p["ETag"], "sha256_b64": p.get("ChecksumSHA256", "")}
                    for p in r.get("Parts", [])]
            if not r.get("IsTruncated"):
                return out
            marker = r["NextPartNumberMarker"]

    def complete(self, key: str, upload_id: str, parts: list[dict]) -> dict:
        self._call("complete_multipart_upload", Key=key, UploadId=upload_id, MultipartUpload={"Parts": [
            {"PartNumber": p["n"], "ETag": p["etag"], "ChecksumSHA256": p["sha256_b64"]} for p in parts]})
        return {"size": self._call("head_object", Key=key)["ContentLength"]}

    def abort(self, key: str, upload_id: str) -> None:
        self._call("abort_multipart_upload", Key=key, UploadId=upload_id)

    def list_prefix(self, prefix: str) -> tuple[list[dict], list[dict]]:
        keys, uploads = [], []
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            keys += [o["Key"] for o in page.get("Contents", [])]
        for page in self.s3.get_paginator("list_multipart_uploads").paginate(Bucket=self.bucket, Prefix=prefix):
            uploads += [{"key": u["Key"], "upload_id": u["UploadId"], "started": iso(u["Initiated"])}
                        for u in page.get("Uploads", [])]
        with ThreadPoolExecutor(8) as pool:      # ListObjectsV2 carries no user metadata: one HEAD per file
            heads = list(pool.map(lambda k: self._call("head_object", Key=k), keys))
        objects = [{"key": k, "size": h["ContentLength"], "sha256": h.get("Metadata", {}).get("sha256", ""),
                    "kind": h.get("Metadata", {}).get("kind", ""), "modified": iso(h["LastModified"])}
                   for k, h in zip(keys, heads)]
        return objects, uploads

    # ---- the database backup ----

    def put_backup(self, local_path) -> None:
        with open(local_path, "rb") as f:
            self._call("put_object", Key=BACKUP_KEY, Body=f)

    def get_backup(self, local_path) -> bool:
        """False only when a listing of backups/ (which the role may make) shows no copy. Any refusal is raised, never
        taken for "no copy", because an empty database started in its place would be backed up over the real one."""
        found = self._call("list_objects_v2", Prefix=BACKUP_KEY, MaxKeys=1).get("Contents", [])
        if not found or found[0]["Key"] != BACKUP_KEY:
            return False
        r = self._call("get_object", Key=BACKUP_KEY)
        with open(local_path, "wb") as out:
            for chunk in r["Body"].iter_chunks(1024**2):
                out.write(chunk)
        return True
