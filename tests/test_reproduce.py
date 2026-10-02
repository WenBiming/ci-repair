from ci_repair.reproduce import compare, package_of, scoped_args, toolchain_version


def e(code, file, line):
    return {"code": code, "file": file, "line": line}


def test_compare_verdicts():
    ci = [e("E0308", "a.rs", 1), e("E0425", "b.rs", 2)]
    assert compare(ci, ci + [e("E0599", "c.rs", 3)])["verdict"] == "exact"
    assert compare(ci, [e("E0308", "a.rs", 1)])["verdict"] == "partial"
    assert compare(ci, [e("E0308", "a.rs", 7)])["verdict"] == "same_file_other_line"
    assert compare(ci, [e("E0001", "z.rs", 1)])["verdict"] == "different"
    assert compare(ci, [])["verdict"] == "no_error"


def test_scoped_args_replaces_workspace():
    assert scoped_args(("--workspace", "--all-targets"), {"milli", "dump"}) == \
        ["--all-targets", "-p", "dump", "-p", "milli"]
    assert scoped_args(("--workspace",), set()) == ["--workspace"]


def test_package_and_toolchain(tmp_path):
    (tmp_path / "rust-toolchain.toml").write_text('[toolchain]\nchannel = "1.98.1"\n')
    (tmp_path / "Cargo.toml").write_text('[workspace]\nmembers = ["crates/milli"]\n')
    crate = tmp_path / "crates" / "milli"
    (crate / "src" / "update").mkdir(parents=True)
    (crate / "Cargo.toml").write_text('[package]\nname = "milli"\n')
    assert package_of(tmp_path, "crates/milli/src/update/mod.rs") == "milli"
    assert package_of(tmp_path, "build.rs") is None
    assert toolchain_version(tmp_path) == "1.98.1"


def test_build_error_without_diagnostics():
    assert compare([e("E0308", "a.rs", 1)], [], "build_error")["verdict"] == "build_error"


def test_touch_sources_skips_git(tmp_path):
    import os
    from ci_repair.reproduce import touch_sources
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("x")
    (tmp_path / "a.rs").write_text("x")
    for p in (tmp_path / ".git" / "HEAD", tmp_path / "a.rs"):
        os.utime(p, (1000, 1000))
    touch_sources(tmp_path)
    assert (tmp_path / "a.rs").stat().st_mtime > 1000
    assert (tmp_path / ".git" / "HEAD").stat().st_mtime == 1000
