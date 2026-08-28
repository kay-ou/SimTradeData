#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Upload release artifacts to Tencent Cloud COS and maintain releases.json index.

This script uses Tencent's COS SDK for large archive uploads when available,
then falls back to a stdlib signed PUT. It atomically updates a releases.json
file that mimics the GitHub Releases API response format.

Usage:
    COS_SECRET_ID=xxx COS_SECRET_KEY=xxx poetry run python scripts/cos_upload.py \
        --file /tmp/simtradedata-cn-2026-06-24.tar.gz \
        --data-manifest data/export/cn/manifest.json \
        --bucket my-bucket \
        --region ap-guangzhou

The releases.json stored on COS is GitHub Releases-style metadata. Customer
download flows should access data through an authorized service that returns
short-lived signed COS URLs.
"""

import argparse
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


def _cos_sign(
    secret_id: str,
    secret_key: str,
    method: str,
    path: str,
    headers: dict[str, str],
    expire: int = 3600,
) -> str:
    """Generate Tencent COS XML API v5 Authorization header value."""
    now = int(time.time())
    key_time = f"{now};{now + expire}"

    sign_key = hmac.new(
        secret_key.encode(), key_time.encode(), hashlib.sha1
    ).hexdigest()

    # Normalize header keys to lowercase for consistent lookup
    norm_headers = {k.lower(): v for k, v in headers.items()}

    signed_headers = sorted(
        k for k in norm_headers
        if k in ("host", "content-type", "content-length", "x-cos-acl")
    )
    header_list = ";".join(signed_headers)
    header_kv = "&".join(
        f"{h}={quote(str(norm_headers[h]), safe='')}"
        for h in signed_headers
    )

    http_string = f"{method.lower()}\n{path}\n\n{header_kv}\n"
    string_to_sign = (
        f"sha1\n{key_time}\n{hashlib.sha1(http_string.encode()).hexdigest()}\n"
    )
    signature = hmac.new(
        sign_key.encode(), string_to_sign.encode(), hashlib.sha1
    ).hexdigest()

    return (
        f"q-sign-algorithm=sha1"
        f"&q-ak={secret_id}"
        f"&q-sign-time={key_time}"
        f"&q-key-time={key_time}"
        f"&q-header-list={header_list}"
        f"&q-url-param-list="
        f"&q-signature={signature}"
    )


def _cos_host(bucket: str, region: str) -> str:
    """Return the COS bucket hostname."""
    return f"{bucket}.cos.{region}.myqcloud.com"


def _cos_request(
    method: str,
    bucket: str,
    region: str,
    key: str,
    secret_id: str,
    secret_key: str,
    data: bytes | None = None,
    content_type: str = "application/octet-stream",
    public_read: bool = False,
    timeout: int = 120,
) -> tuple[int, bytes]:
    """Make an authenticated COS XML API request. Returns (status, body)."""
    host = _cos_host(bucket, region)
    url = f"https://{host}/{quote(key, safe='/')}"
    path = f"/{key}"

    headers: dict[str, str] = {
        "Host": host,
        "Content-Type": content_type,
    }
    if data is not None:
        headers["Content-Length"] = str(len(data))
    if public_read:
        headers["x-cos-acl"] = "public-read"

    headers["Authorization"] = _cos_sign(secret_id, secret_key, method, path, headers)

    req = Request(url, data=data, headers=headers, method=method.upper())
    try:
        resp = urlopen(req, timeout=timeout)
        return resp.status, resp.read()
    except HTTPError as e:
        return e.code, e.read()


def upload_file(
    bucket: str,
    region: str,
    key: str,
    file_path: Path,
    secret_id: str,
    secret_key: str,
) -> bool:
    """Upload a file to COS. Returns True on success."""
    file_size_mb = file_path.stat().st_size / 1024 / 1024
    print(f"  Uploading {file_path.name} → cos://{bucket}/{key} ({file_size_mb:.1f} MB) ...")

    sdk_result = _upload_file_with_sdk(bucket, region, key, file_path, secret_id, secret_key)
    if sdk_result is not None:
        return sdk_result

    data = file_path.read_bytes()
    status, body = _cos_request(
        "PUT", bucket, region, key, secret_id, secret_key,
        data=data, content_type="application/gzip", public_read=False, timeout=600,
    )
    if status == 200:
        print("  ✓ Uploaded")
        return True
    print(f"  ✗ Upload failed: HTTP {status}")
    if body:
        print(f"    {body.decode(errors='replace')[:500]}")
    return False


def _sha256_file(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _upload_file_with_sdk(
    bucket: str,
    region: str,
    key: str,
    file_path: Path,
    secret_id: str,
    secret_key: str,
) -> bool | None:
    """Upload with qcloud_cos multipart support when the SDK is installed."""
    try:
        from qcloud_cos import CosConfig, CosS3Client
    except ImportError:
        return None

    try:
        part_size_mb = int(os.environ.get("COS_UPLOAD_PART_SIZE_MB", "64"))
        max_threads = int(os.environ.get("COS_UPLOAD_THREADS", "1"))
        config = CosConfig(
            Region=region,
            SecretId=secret_id,
            SecretKey=secret_key,
            Scheme="https",
            Timeout=int(os.environ.get("COS_UPLOAD_TIMEOUT_SECONDS", "600")),
            PoolConnections=max_threads,
            PoolMaxSize=max_threads,
        )
        client = CosS3Client(config)
        client.upload_file(
            Bucket=bucket,
            Key=key,
            LocalFilePath=str(file_path),
            PartSize=part_size_mb,
            MAXThread=max_threads,
            EnableMD5=False,
        )
    except Exception as exc:
        print(f"  ✗ SDK upload failed: {exc}")
        return False

    print("  ✓ Uploaded")
    return True


def _fetch_releases_json(
    bucket: str,
    region: str,
    secret_id: str,
    secret_key: str,
    *,
    strict: bool = False,
) -> list[dict]:
    """Download releases.json from COS. Returns empty list if not found."""
    status, body = _cos_request(
        "GET", bucket, region, "releases.json", secret_id, secret_key,
        content_type="application/json", timeout=30,
    )
    if status == 200:
        return json.loads(body.decode())
    if status == 404:
        return []
    if strict:
        raise RuntimeError(f"failed to fetch releases.json (HTTP {status})")
    print(f"  Warning: failed to fetch releases.json (HTTP {status})")
    return []


def _put_releases_json(
    bucket: str, region: str, releases: list[dict], secret_id: str, secret_key: str
) -> bool:
    """Upload releases.json to COS."""
    data = json.dumps(releases, ensure_ascii=False, indent=2).encode()
    status, body = _cos_request(
        "PUT", bucket, region, "releases.json", secret_id, secret_key,
        data=data, content_type="application/json", public_read=False, timeout=30,
    )
    if status == 200:
        print(f"  ✓ releases.json updated ({len(releases)} releases)")
        return True
    print(f"  ✗ Failed to update releases.json: HTTP {status}")
    return False


def _release_metadata(data_manifest: dict) -> tuple[str, dict]:
    market = data_manifest.get("market", "")
    if data_manifest.get("package_format") == "simtradedata_api_delta_v1":
        base_version = data_manifest.get("from_version", "")
        target_version = data_manifest.get("to_version", "")
        tag = (
            f"data-{market.lower()}-{base_version}-to-{target_version}-delta"
        )
        return tag, {
            "release_type": "delta",
            "market": market.upper(),
            "base_version": base_version,
            "target_version": target_version,
        }

    target_version = data_manifest.get("version", "")
    return f"data-{market.lower()}-{target_version}", {
        "release_type": "baseline",
        "market": market.upper(),
        "target_version": target_version,
    }


def _latest_published_version(releases: list[dict], market: str) -> str:
    """Return the latest valid target version published for a market."""
    normalized_market = market.lower()
    legacy_tag = re.compile(
        rf"^data-{re.escape(normalized_market)}-(\d{{4}}-\d{{2}}-\d{{2}})$"
    )
    versions = []

    for release in releases:
        release_market = str(release.get("market", "")).lower()
        target_version = release.get("target_version")
        if release_market == normalized_market and isinstance(target_version, str):
            candidate = target_version
        else:
            match = legacy_tag.fullmatch(str(release.get("tag_name", "")))
            if not match:
                continue
            candidate = match.group(1)

        try:
            dt.date.fromisoformat(candidate)
        except ValueError:
            continue
        versions.append(candidate)

    return max(versions, default="")


def _build_release_entry(
    data_manifest: dict, archive_path: Path, archive_size: int,
    tag: str, bucket: str, region: str, cos_key: str,
) -> dict:
    """Build a GitHub-API-compatible release entry from data manifest + archive."""
    archive_name = archive_path.name
    archive_sha256 = _sha256_file(archive_path)

    # Brief release body
    market = data_manifest.get("market", "")
    _, release_metadata = _release_metadata(data_manifest)
    version = release_metadata["target_version"]
    date_range = data_manifest.get("date_range", {})
    body_lines = [
        f"Market: {market}",
        f"Version: {version}",
        f"Date range: {date_range.get('start', 'N/A')} ~ {date_range.get('end', 'N/A')}",
        f"Archive: {archive_name} ({archive_size / 1024 / 1024:.1f} MB)",
    ]

    return {
        **release_metadata,
        "tag_name": tag,
        "name": f"SimTradeData {market} {version}",
        "body": "\n".join(body_lines),
        "assets": [
            {
                "name": archive_name,
                "size": archive_size,
                "sha256": archive_sha256,
                "browser_download_url": (
                    f"https://{_cos_host(bucket, region)}/{cos_key}"
                ),
            }
        ],
    }


def _update_releases_index(
    bucket: str,
    region: str,
    secret_id: str,
    secret_key: str,
    tag: str,
    entry: dict,
    max_releases: int,
) -> bool:
    """Fetch releases.json, prepend a new entry, trim old ones, and upload.

    Returns True on success.
    """
    try:
        releases = _fetch_releases_json(
            bucket, region, secret_id, secret_key, strict=True
        )
    except (RuntimeError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return False

    # Remove existing entry for this tag (idempotent re-upload)
    releases = [r for r in releases if r.get("tag_name") != tag]

    # Prepend new entry
    releases.insert(0, entry)

    # Trim old entries
    if len(releases) > max_releases:
        trimmed = len(releases) - max_releases
        releases = releases[:max_releases]
        print(f"  Trimmed {trimmed} old releases")

    return _put_releases_json(bucket, region, releases, secret_id, secret_key)


def main():
    parser = argparse.ArgumentParser(
        description="Upload release artifacts to Tencent COS"
    )
    parser.add_argument("--file", help="tar.gz archive to upload")
    parser.add_argument(
        "--data-manifest",
        help="Data manifest.json from export_parquet.py output directory",
    )
    parser.add_argument("--bucket", required=True, help="COS bucket name")
    parser.add_argument("--region", required=True, help="COS region (e.g. ap-guangzhou)")
    parser.add_argument("--market", choices=["cn", "us"])
    parser.add_argument(
        "--print-latest-version",
        action="store_true",
        help="Print the latest published target version for --market",
    )
    phase_group = parser.add_mutually_exclusive_group()
    phase_group.add_argument(
        "--skip-index",
        action="store_true",
        help="Upload the archive without updating releases.json",
    )
    phase_group.add_argument(
        "--index-only",
        action="store_true",
        help="Update releases.json without uploading the archive",
    )
    parser.add_argument(
        "--key-prefix", default="",
        help="COS key prefix / directory (e.g. 'data/')",
    )
    parser.add_argument(
        "--max-releases", type=int, default=120,
        help=(
            "Keep at most this many releases in releases.json (default: 120). "
            "Each delta needs every intermediate release in its chain; too short "
            "a window breaks the chain and forces older clients back to a full "
            "download."
        ),
    )
    args = parser.parse_args()

    secret_id = os.environ.get("COS_SECRET_ID")
    secret_key = os.environ.get("COS_SECRET_KEY")
    if not secret_id or not secret_key:
        print("ERROR: COS_SECRET_ID and COS_SECRET_KEY environment variables required")
        sys.exit(1)

    if args.print_latest_version:
        if not args.market:
            parser.error("--market is required with --print-latest-version")
        try:
            releases = _fetch_releases_json(
                args.bucket,
                args.region,
                secret_id,
                secret_key,
                strict=True,
            )
        except (RuntimeError, json.JSONDecodeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)
        print(_latest_published_version(releases, args.market))
        return

    if not args.file or not args.data_manifest:
        parser.error("--file and --data-manifest are required for upload")

    file_path = Path(args.file)
    if not file_path.exists():
        print(f"ERROR: file not found: {args.file}")
        sys.exit(1)

    manifest_path = Path(args.data_manifest)
    if not manifest_path.exists():
        print(f"ERROR: manifest not found: {args.data_manifest}")
        sys.exit(1)
    data_manifest = json.loads(manifest_path.read_text())

    tag, release_metadata = _release_metadata(data_manifest)
    if release_metadata["release_type"] == "delta":
        if not all(
            (
                release_metadata["market"],
                release_metadata["base_version"],
                release_metadata["target_version"],
            )
        ):
            print("ERROR: delta manifest missing market, from_version, or to_version field")
            sys.exit(1)
    elif not release_metadata["market"] or not release_metadata["target_version"]:
        print("ERROR: manifest missing market or version field")
        sys.exit(1)

    versions = (
        (
            ("delta manifest from_version", release_metadata["base_version"]),
            ("delta manifest to_version", release_metadata["target_version"]),
        )
        if release_metadata["release_type"] == "delta"
        else (("baseline manifest version", release_metadata["target_version"]),)
    )
    parsed_versions = {}
    for label, version in versions:
        try:
            parsed = dt.date.fromisoformat(version)
        except (TypeError, ValueError):
            print(f"ERROR: {label} must be an ISO date (YYYY-MM-DD)")
            sys.exit(1)
        if parsed.isoformat() != version:
            print(f"ERROR: {label} must be an exact ISO date (YYYY-MM-DD)")
            sys.exit(1)
        parsed_versions[label] = parsed

    if release_metadata["release_type"] == "delta" and (
        parsed_versions["delta manifest from_version"]
        >= parsed_versions["delta manifest to_version"]
    ):
        print("ERROR: delta manifest from_version must be earlier than to_version")
        sys.exit(1)

    key_prefix = args.key_prefix.strip("/")
    cos_key = f"{key_prefix}/{file_path.name}" if key_prefix else file_path.name

    print(f"COS Upload: {_cos_host(args.bucket, args.region)}")
    print(f"  Tag:  {tag}")
    print(f"  File: {file_path.name}")

    # 1. Upload archive
    if not args.index_only:
        if not upload_file(
            args.bucket, args.region, cos_key, file_path, secret_id, secret_key
        ):
            sys.exit(1)

    # 2. Update releases index
    if not args.skip_index:
        file_size = file_path.stat().st_size
        entry = _build_release_entry(
            data_manifest, file_path, file_size,
            tag, args.bucket, args.region, cos_key,
        )
        if not _update_releases_index(
            args.bucket, args.region, secret_id, secret_key,
            tag, entry, args.max_releases,
        ):
            sys.exit(1)

    print("Done.")


if __name__ == "__main__":
    main()
