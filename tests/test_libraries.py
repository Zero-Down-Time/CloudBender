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
    fetch_library(conn, None, "global", "s3://b/libs/net",
                  "latest", str(tmp_path))
    assert conn.calls[0][2]["Key"] == "libs/net-latest.tar.gz"


def test_fetch_unsupported_scheme(tmp_path):
    with pytest.raises(NotImplementedError):
        fetch_library(None, None, None, "oci://registry/lib",
                      "latest", str(tmp_path))


def test_fetch_allows_absolute_symlinks(tmp_path):
    # CloudFormation asset bundles commonly ship symlinks (incl. absolute
    # targets); the archive must still unpack so the template is reachable
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = b"Description: t\n"
        fi = tarfile.TarInfo("cloudformation/vpc.yaml.jinja")
        fi.size = len(data)
        tar.addfile(fi, io.BytesIO(data))
        li = tarfile.TarInfo("artifacts/abs.sh")
        li.type = tarfile.SYMTYPE
        li.linkname = "/etc/hosts"
        tar.addfile(li)
    conn = FakeConn(buf.getvalue())

    lib_root = fetch_library(
        conn, None, "global", "s3://b/libs/cfn-lib", "latest", str(tmp_path))

    assert (lib_root / "cloudformation" / "vpc.yaml.jinja").is_file()
    assert (lib_root / "artifacts" / "abs.sh").is_symlink()


def test_fetch_local_uses_path_directly(tmp_path):
    lib = tmp_path / "mylib"
    (lib / "pulumi").mkdir(parents=True)
    (lib / "pulumi" / "vpc.py").write_text("x = 1\n")

    dest = tmp_path / "workdir"
    dest.mkdir()

    lib_root = fetch_library(
        None, None, None, "local://{}".format(lib), "latest", str(dest))

    # returned as-is, nothing copied into the work dir
    assert lib_root == lib.resolve()
    assert (lib_root / "pulumi" / "vpc.py").is_file()
    assert list(dest.iterdir()) == []


def test_fetch_local_relative_to_root(tmp_path):
    (tmp_path / "libs" / "net" / "pulumi").mkdir(parents=True)

    lib_root = fetch_library(
        None, None, None, "local://libs/net", "latest", "/tmp",
        root=str(tmp_path))

    assert lib_root == (tmp_path / "libs" / "net").resolve()


def test_fetch_local_relative_falls_back_to_cwd(tmp_path, monkeypatch):
    (tmp_path / "libs" / "net" / "pulumi").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    lib_root = fetch_library(
        None, None, None, "local://libs/net", "latest", "/tmp")

    assert lib_root == (tmp_path / "libs" / "net").resolve()


def test_fetch_local_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        fetch_library(None, None, None, "local://{}/nope".format(tmp_path),
                      "latest", str(tmp_path))


def test_fetch_rejects_path_traversal(tmp_path):
    conn = FakeConn(_make_targz({"../evil.py": "pwned\n"}))
    with pytest.raises(ValueError):
        fetch_library(conn, None, "global", "s3://b/libs/bad",
                      "1.0", str(tmp_path))
    assert not (tmp_path.parent / "evil.py").exists()
