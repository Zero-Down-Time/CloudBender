import io
import pathlib
import tarfile
import urllib.parse

import logging

from .utils import get_s3_url

logger = logging.getLogger(__name__)


def fetch_library(conn, profile, region, url, version, dest_dir):
    """Fetch and unpack a Pulumi library archive into dest_dir.

    The archive object is derived as <url>-<version>.tar.gz and is expected
    to contain a top-level 'pulumi/' directory. Returns the unpacked root.
    """
    archive_url = "{}-{}.tar.gz".format(url, version)
    scheme = urllib.parse.urlparse(archive_url).scheme

    if scheme == "s3":
        return _fetch_s3(conn, profile, region, archive_url, dest_dir)

    raise NotImplementedError(
        "Unsupported library protocol '{}://' ({}); only s3:// is supported".format(
            scheme, archive_url
        )
    )


def _fetch_s3(conn, profile, region, archive_url, dest_dir):
    bucket, key = get_s3_url(archive_url)

    try:
        response = conn.call(
            "s3",
            "get_object",
            {"Bucket": bucket, "Key": key},
            profile=profile,
            region=region,
        )
        body = response["Body"].read()
    except Exception as e:
        raise FileNotFoundError(
            "Could not fetch library s3://{}/{}: {}".format(bucket, key, e)
        ) from None

    name = pathlib.PurePosixPath(key).name.removesuffix(".tar.gz")
    lib_root = pathlib.Path(dest_dir) / name
    _extract(body, lib_root, archive_url)

    logger.info("Fetched library {} to {}".format(archive_url, lib_root))
    return lib_root


def _extract(body, lib_root, archive_url):
    lib_root.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as tar:
            # filter="data" rejects absolute paths and traversal (Python 3.12+)
            tar.extractall(path=lib_root, filter="data")
    except Exception as e:
        raise ValueError(
            "Could not unpack library {}: {}".format(archive_url, e)
        ) from None
