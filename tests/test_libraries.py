import io
import tarfile

import pytest

from cloudbender.libraries import fetch_library


class FakeBody:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data


class FakeConn:
    def __init__(self, data):
        self._data = data
        self.calls = []

    def call(self, service, command, kwargs={}, profile=None, region=None):
        self.calls.append((service, command, kwargs, profile, region))
        return {"Body": FakeBody(self._data)}


def _make_targz(members):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in members.items():
            data = content.encode()
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_fetch_s3_unpacks_and_interpolates_version(tmp_path):
    archive = _make_targz({"pulumi/vpc.py": "VERSION = '1.0'\n"})
    conn = FakeConn(archive)

    lib_root = fetch_library(
        conn, "myprofile", "eu-central-1",
        "s3://my-bucket/libs/vpc-lib", "1.2.3", str(tmp_path))

    service, command, kwargs, profile, region = conn.calls[0]
    assert service == "s3" and command == "get_object"
    assert kwargs["Bucket"] == "my-bucket"
    assert kwargs["Key"] == "libs/vpc-lib-1.2.3.tar.gz"
    assert profile == "myprofile" and region == "eu-central-1"

    assert (lib_root / "pulumi" / "vpc.py").is_file()


def test_fetch_latest_version_literal(tmp_path):
    conn = FakeConn(_make_targz({"pulumi/net.py": "x = 1\n"}))
    fetch_library(conn, None, "global", "s3://b/libs/net", "latest", str(tmp_path))
    assert conn.calls[0][2]["Key"] == "libs/net-latest.tar.gz"


def test_fetch_unsupported_scheme(tmp_path):
    with pytest.raises(NotImplementedError):
        fetch_library(None, None, None, "oci://registry/lib",
                      "latest", str(tmp_path))


def test_fetch_rejects_path_traversal(tmp_path):
    conn = FakeConn(_make_targz({"../evil.py": "pwned\n"}))
    with pytest.raises(ValueError):
        fetch_library(conn, None, "global", "s3://b/libs/bad",
                      "1.0", str(tmp_path))
    assert not (tmp_path.parent / "evil.py").exists()
