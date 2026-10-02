import json

from ci_repair.rustdiag import clean_log, from_cargo_json, from_ci_log, parse_merge_line

CI_LOG = """\
2026-09-16T12:31:29.5379709Z HEAD is now at d2d9e88 Merge 5a3ebe351eed598661ca247119be23c1c6943dce into bae5f5658eb988358f01ba6186d08015723ecc60
2026-09-16T12:33:01.0000000Z    Compiling meilisearch v1.54.0 (/home/runner/work/meilisearch/meilisearch/crates/meilisearch)
2026-09-16T12:33:01.1000000Z \x1b[0m\x1b[1m\x1b[38;5;9merror[E0308]\x1b[0m\x1b[0m\x1b[1m: mismatched types\x1b[0m
2026-09-16T12:33:01.1000000Z     --> crates/meilisearch/src/routes/mcp.rs:193:49
2026-09-16T12:33:01.1000000Z      |
2026-09-16T12:33:01.1000000Z  193 |             error: Some(McpError::unknow_method(unknow_method_name)),
2026-09-16T12:33:01.1000000Z      |                         ----------------------- ^^^^^^^^^^^^^^^^^^ expected `&str`, found `McpMethod`
2026-09-16T12:33:01.1000000Z note: associated function defined here
2026-09-16T12:33:01.1000000Z     --> crates/meilisearch/src/routes/mcp.rs:1025:8
2026-09-16T12:33:01.1000000Z
2026-09-16T12:33:01.1000000Z error: unused import: `std::fmt`
2026-09-16T12:33:01.1000000Z  --> /home/runner/work/meilisearch/meilisearch/crates/milli/src/lib.rs:3:5
2026-09-16T12:33:01.1000000Z   |
2026-09-16T12:33:01.1000000Z 3 | use std::fmt;
2026-09-16T12:33:01.1000000Z   |     ^^^^^^^^
2026-09-16T12:33:01.1000000Z   |
2026-09-16T12:33:01.1000000Z   = note: `-D unused-imports` implied by `-D warnings`
2026-09-16T12:33:01.1000000Z
2026-09-16T12:33:01.1000000Z error: this `if` has identical blocks
2026-09-16T12:33:01.1000000Z   --> crates/milli/src/update.rs:10:5
2026-09-16T12:33:01.1000000Z    = help: for further information visit https://rust-lang.github.io/rust-clippy/master/index.html#if_same_then_else
2026-09-16T12:33:01.1000000Z    = note: `-D clippy::if-same-then-else` implied by `-D warnings`
2026-09-16T12:33:01.1000000Z
2026-09-16T12:33:01.1000000Z error: expected one of `,`, `.`, `?`, or an operator, found `}`
2026-09-16T12:33:01.1000000Z   --> crates/dump/src/reader/mod.rs:42:1
2026-09-16T12:33:01.1000000Z
2026-09-16T12:33:01.1000000Z For more information about this error, try `rustc --explain E0308`.
2026-09-16T12:33:01.1000000Z error: could not compile `meilisearch` (lib) due to 1 previous error
2026-09-16T12:33:01.1000000Z ##[error]Process completed with exit code 101.
"""


def test_ci_log_extracts_primary_location_and_kind():
    diags = from_ci_log(CI_LOG)
    got = [(d.kind, d.code, d.file, d.line, d.col) for d in diags]
    assert got == [
        ("rustc", "E0308", "crates/meilisearch/src/routes/mcp.rs", 193, 49),
        ("rustc_lint", "unused_imports", "crates/milli/src/lib.rs", 3, 5),
        ("clippy", "clippy::if_same_then_else", "crates/milli/src/update.rs", 10, 5),
        ("rustc_nocode", None, "crates/dump/src/reader/mod.rs", 42, 1),
    ]
    assert diags[0].message == "mismatched types"


def test_compile_errors_exclude_clippy():
    diags = from_ci_log(CI_LOG)
    assert [d.kind for d in diags if d.is_compile_error] == ["rustc", "rustc_lint", "rustc_nocode"]


def test_merge_line():
    assert parse_merge_line(CI_LOG) == (
        "5a3ebe351eed598661ca247119be23c1c6943dce",
        "bae5f5658eb988358f01ba6186d08015723ecc60",
    )
    assert parse_merge_line("no checkout here") is None


def test_clean_log_strips_timestamps_and_ansi():
    assert clean_log("2026-09-16T12:33:01.1000000Z \x1b[1merror\x1b[0m: x") == "error: x"


def _msg(level, code, message, file, line, col, children=()):
    return json.dumps({
        "reason": "compiler-message",
        "message": {
            "level": level, "message": message,
            "code": {"code": code} if code else None,
            "spans": [{"file_name": file, "line_start": line, "column_start": col,
                       "is_primary": True}],
            "children": [{"message": c, "spans": []} for c in children],
        },
    })


def test_cargo_json():
    lines = "\n".join([
        json.dumps({"reason": "compiler-artifact"}),
        _msg("error", "E0308", "mismatched types", "crates/a/src/x.rs", 1, 2),
        _msg("error", "unused_imports", "unused import: `y`", "crates/a/src/y.rs", 3, 4,
             ["`-D unused-imports` implied by `-D warnings`"]),
        _msg("warning", "dead_code", "never used", "crates/a/src/z.rs", 5, 6),
        json.dumps({"reason": "compiler-message", "message": {
            "level": "error", "message": "aborting due to 2 previous errors",
            "code": None, "spans": [], "children": []}}),
        "not json",
    ])
    diags = from_cargo_json(lines)
    assert [(d.kind, d.code, d.file, d.line) for d in diags] == [
        ("rustc", "E0308", "crates/a/src/x.rs", 1),
        ("rustc_lint", "unused_imports", "crates/a/src/y.rs", 3),
    ]


REPEATED_LINT_LOG = """\
error: unused variable: `features`
 --> crates/a/src/x.rs:5:9
  = note: `-D unused-variables` implied by `-D warnings`

error: unused variable: `progress`
 --> crates/a/src/x.rs:9:9

error: unused imports: `A`, `B`
 --> crates/a/src/y.rs:1:5

error: use of deprecated function `f`: fixme
 --> crates/a/src/z.rs:2:3
"""


def test_repeated_lints_without_note_are_still_lints():
    got = [(d.kind, d.code) for d in from_ci_log(REPEATED_LINT_LOG)]
    assert got == [
        ("rustc_lint", "unused_variables"),   # note present
        ("rustc_lint", "unused_variables"),   # learned from the earlier occurrence
        ("rustc_lint", "unused_imports"),     # known message prefix
        ("rustc_lint", "deprecated"),
    ]
