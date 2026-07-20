import io
import pathlib
import tarfile
import urllib.parse

import logging

from .utils import get_s3_url

logger = logging.getLogger(__name__)


def fetch_library(conn, profile, region, url, version, dest_dir, root=None):
    """Resolve a Pulumi library to a local directory root.

    The returned root is expected to contain a top-level 'pulumi/' directory.
    For remote protocols the archive <url>-<version>.tar.gz is fetched and
    unpacked into dest_dir. 'local://' points directly at an existing
    directory and is neither fetched nor copied; relative paths resolve
    against root (the CloudBender project directory).
    """
    scheme = urllib.parse.urlparse(url).scheme

    if scheme == "local":
        return _local_path(url, root)

    if scheme == "s3":
        archive_url = "{}-{}.tar.gz".format(url, version)
        return _fetch_s3(conn, profile, region, archive_url, dest_dir)

    raise NotImplementedError(
        "Unsupported library protocol '{}://' ({}); supported: local, s3".format(
            scheme, url
        )
    )


def _local_path(url, root=None):
    """Return the directory referenced by a local:// URL.

    Relative paths resolve against root (the CloudBender project directory),
    falling back to the current working directory when root is unset.
    """
    # strip 'local://' by hand; urlparse folds relative paths into netloc
    path = pathlib.Path(url[len("local://"):]).expanduser()

    if not path.is_absolute():
        path = pathlib.Path(root if root else pathlib.Path.cwd()) / path

    lib_root = path.resolve()

    if not lib_root.is_dir():
        raise FileNotFoundError(
            "Local library path does not exist: {} ({})".format(lib_root, url)
        )

    logger.info("Using local library {}".format(lib_root))
    return lib_root


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
