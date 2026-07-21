import io
import tarfile

import pytest

from cloudbender.stack import Stack


class _FakeBody:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data


class _FakeConn:
    def __init__(self, data):
        self._data = data

    def call(self, service, command, kwargs={}, profile=None, region=None):
        return {"Body": _FakeBody(self._data)}


def _targz_file(path, content):
    buf = io.BytesIO()
    data = content.encode()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name=path)
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _make_stack(tmp_path, libraries):
    ctx = {"root": str(tmp_path), "region": None, "profile": None}
    stack = Stack(
        name="vpc",
        template="vpc",
        path=tmp_path / "vpc.yaml",
        rel_path="",
        ctx=ctx,
    )
    stack.libraries = libraries
    return stack


def _make_lib(root, name, subdirs):
    lib = root / name
    for sub in subdirs:
        (lib / sub).mkdir(parents=True)
    return lib


def test_fetch_libraries_collects_buckets(tmp_path):
    lib1 = _make_lib(tmp_path, "lib1", ["pulumi", "cloudformation"])
    lib2 = _make_lib(tmp_path, "lib2", ["artifacts", "policies"])

    stack = _make_stack(tmp_path, [
        {"url": "local://{}".format(lib1)},
        {"url": "local://{}".format(lib2)},
    ])

    paths = stack._fetch_libraries()

    assert paths["pulumi"] == [str(lib1 / "pulumi")]
    assert paths["cloudformation"] == [str(lib1 / "cloudformation")]
    assert paths["artifacts"] == [str(lib2 / "artifacts")]
    assert paths["policies"] == [str(lib2 / "policies")]
    # policy_paths mirrored onto the stack for _set_pulumi_args
    assert stack.policy_paths == [str(lib2 / "policies")]
    assert stack.work_dir and stack.work_dir.startswith("/")


def test_fetch_libraries_preserves_config_order(tmp_path):
    lib1 = _make_lib(tmp_path, "lib1", ["cloudformation"])
    lib2 = _make_lib(tmp_path, "lib2", ["cloudformation"])

    stack = _make_stack(tmp_path, [
        {"url": "local://{}".format(lib2)},
        {"url": "local://{}".format(lib1)},
    ])

    paths = stack._fetch_libraries()
    assert paths["cloudformation"] == [
        str(lib2 / "cloudformation"), str(lib1 / "cloudformation")]


def test_fetch_libraries_optional_skipped(tmp_path):
    lib1 = _make_lib(tmp_path, "lib1", ["cloudformation"])

    stack = _make_stack(tmp_path, [
        {"url": "local://{}/missing".format(tmp_path), "optional": True},
        {"url": "local://{}".format(lib1)},
    ])

    paths = stack._fetch_libraries()
    assert paths["cloudformation"] == [str(lib1 / "cloudformation")]


def test_fetch_libraries_required_missing_raises(tmp_path):
    stack = _make_stack(tmp_path, [
        {"url": "local://{}/missing".format(tmp_path)},
    ])

    with pytest.raises(FileNotFoundError):
        stack._fetch_libraries()


def test_fetch_libraries_records_loaded(tmp_path):
    lib1 = _make_lib(tmp_path, "lib1", ["cloudformation"])

    stack = _make_stack(tmp_path, [
        {"url": "local://{}/missing".format(tmp_path), "optional": True},
        {"url": "local://{}".format(lib1)},
    ])

    stack._fetch_libraries()
    # only the successfully loaded library is recorded, skipped optional isn't;
    # local:// ignores version so no @version suffix
    assert stack.loaded_libraries == ["local://{}".format(lib1)]


def test_fetch_libraries_records_explicit_version_for_remote(tmp_path):
    conn = _FakeConn(_targz_file("cloudformation/vpc.yaml.jinja", "x"))
    stack = _make_stack(
        tmp_path, [{"url": "s3://b/libs/vpc-lib", "version": "1.2.3"}])
    stack.connection_manager = conn

    stack._fetch_libraries()
    assert stack.loaded_libraries == ["s3://b/libs/vpc-lib@1.2.3"]


def test_fetch_libraries_records_default_latest_for_remote(tmp_path):
    conn = _FakeConn(_targz_file("cloudformation/vpc.yaml.jinja", "x"))
    stack = _make_stack(tmp_path, [{"url": "s3://b/libs/net"}])
    stack.connection_manager = conn

    stack._fetch_libraries()
    assert stack.loaded_libraries == ["s3://b/libs/net@latest"]


def test_render_missing_template_lists_loaded_libraries(tmp_path):
    lib1 = _make_lib(tmp_path, "lib1", ["cloudformation"])

    stack = _make_stack(tmp_path, [
        {"url": "local://{}".format(lib1)},
    ])
    stack.mode = "CloudBender"

    with pytest.raises(FileNotFoundError) as excinfo:
        stack.render()

    msg = str(excinfo.value)
    assert "local://{}".format(lib1) in msg
    assert stack.stackname in msg
    # work_dir is cleaned up even on the failure path
    assert not stack.work_dir or not __import__(
        "os").path.exists(stack.work_dir)
