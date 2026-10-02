"""Parse rustc/cargo diagnostics from GitHub Actions logs and from `cargo --message-format=json`.

Both sources are reduced to the same `Diagnostic` record so that a CI failure and a
local reproduction can be compared by (code, file, line).

Kinds:
  rustc         error with an E-code (type errors, unresolved names, ...)
  rustc_lint    rustc lint promoted to an error (RUSTFLAGS="-D warnings" or #[deny])
  rustc_nocode  error without code, e.g. parse errors
  clippy        clippy lint; not a compile error for this thesis
"""

import json
import re
from pathlib import PurePosixPath as Path
from dataclasses import asdict, dataclass

TS_PREFIX = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.]+Z ?", re.M)
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
HEADER = re.compile(r"^error(?:\[(E\d{4})\])?: (.+)$")
LOCATION = re.compile(r"^\s*--> (.+?):(\d+):(\d+)\s*$")
LINT_FLAG = re.compile(r"`-D ([\w:-]+)` implied by|`#\[deny\(([\w:]+)\)\]`")
CLIPPY_URL = re.compile(r"rust-clippy/[^#\s]*#(\w+)")
MERGE = re.compile(r"HEAD is now at \w+ Merge ([0-9a-f]{40}) into ([0-9a-f]{40})")
PR_REF = re.compile(r"refs/remotes/pull/(\d+)/merge")
# cargo/rustc summary lines that look like errors but carry no diagnostic
SUMMARY = re.compile(r"^(could not compile|aborting due to|build failed|"
                     r"failed to run custom build command|process didn't exit)")
# rustc prints the "`-D lint` implied by `-D warnings`" note only on a lint's first
# occurrence; later ones are recognised by their message.
KNOWN_LINT_MESSAGES = [
    (re.compile(r"^unused imports?\b"), "unused_imports"),
    (re.compile(r"^unused variables?\b"), "unused_variables"),
    (re.compile(r"^variables? does not need to be mutable"), "unused_mut"),
    (re.compile(r"^unreachable (expression|statement|call)"), "unreachable_code"),
    (re.compile(r"^unreachable pattern"), "unreachable_patterns"),
    (re.compile(r"^use of deprecated "), "deprecated"),
    (re.compile(r"^unused `.+` that must be used|^unused return value"), "unused_must_use"),
    (re.compile(r"^(\w+ )?`.+` is never (used|read|constructed)|"
                r"^(fields?|variants?|methods?|associated items?) .* never "), "dead_code"),
    (re.compile(r"^unnecessary (parentheses|braces)"), "unused_parens"),
    (re.compile(r"^unexpected `cfg` condition"), "unexpected_cfgs"),
    (re.compile(r"^unused doc comment"), "unused_doc_comments"),
    (re.compile(r"^unused macro definition"), "unused_macros"),
]
CI_ROOT =re.compile(r"^(?:/home/runner/work|D:\\a|/Users/runner/work)[/\\][^/\\]+[/\\][^/\\]+[/\\]")


@dataclass(frozen=True)
class Diagnostic:
    kind: str
    code: str | None
    message: str
    file: str
    line: int
    col: int

    @property
    def is_compile_error(self):
        return self.kind != "clippy"

    @property
    def key(self):
        """Identity used to match CI errors against reproduced ones."""
        return (self.code, self.file, self.line)

    def to_dict(self):
        return asdict(self)


def clean_log(text):
    return ANSI.sub("", TS_PREFIX.sub("", text))


def normalize_path(path):
    return CI_ROOT.sub("", path).replace("\\", "/")


def _lint_name(flag):
    return flag.replace("-", "_")


def _classify(code, block):
    """kind and code for one diagnostic, given its E-code (or None) and its text block."""
    if code:
        return "rustc", code
    m = LINT_FLAG.search(block)
    lint = _lint_name(m.group(1) or m.group(2)) if m else None
    url = CLIPPY_URL.search(block)
    if (lint and lint.startswith("clippy::")) or url:
        return "clippy", lint if lint and lint.startswith("clippy::") else f"clippy::{url.group(1)}"
    if lint and lint != "warnings":
        return "rustc_lint", lint
    return "rustc_nocode", None


def _message_stem(message):
    return re.sub(r"`[^`]*`", "``", message).rstrip(": ")


def _lint_from_message(message, learned):
    if _message_stem(message) in learned:
        return learned[_message_stem(message)]
    return next((lint for rx, lint in KNOWN_LINT_MESSAGES if rx.search(message)), None)


def from_ci_log(text):
    """Error diagnostics in a CI job log, in order of appearance, deduplicated."""
    lines = clean_log(text).splitlines()
    starts = [i for i, l in enumerate(lines) if HEADER.match(l)]
    out, seen, learned = [], set(), {}
    for n, i in enumerate(starts):
        code, message = HEADER.match(lines[i]).groups()
        if SUMMARY.match(message):
            continue
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        block_lines = lines[i:end]
        loc = next((LOCATION.match(l) for l in block_lines[1:4] if LOCATION.match(l)), None)
        if not loc:
            continue
        kind, code = _classify(code, "\n".join(block_lines))
        if kind == "rustc_lint":
            learned[_message_stem(message)] = code
        elif kind == "rustc_nocode":
            lint = _lint_from_message(message, learned)
            if lint:
                kind, code = "rustc_lint", lint
        d = Diagnostic(kind, code, message.strip(), normalize_path(loc.group(1)),
                       int(loc.group(2)), int(loc.group(3)))
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def _call_site(span):
    """Follow macro expansions out of dependency code to the project's call site.

    rustc's text output (as in CI logs) reports such errors where the macro is used,
    while the JSON primary span points into the macro's own (dependency) source.
    """
    while span.get("expansion") and Path(span["file_name"]).is_absolute():
        span = span["expansion"]["span"]
    return span


def from_cargo_json(text):
    """Error diagnostics from `cargo ... --message-format=json` output."""
    out, seen = [], set()
    for raw in text.splitlines():
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if rec.get("reason") != "compiler-message":
            continue
        msg = rec["message"]
        if msg.get("level") != "error":
            continue
        span = next((s for s in msg.get("spans", []) if s.get("is_primary")), None)
        if not span:
            continue
        span = _call_site(span)
        code = (msg.get("code") or {}).get("code")
        children = "\n".join(c.get("message", "") for c in msg.get("children", []))
        if code and re.fullmatch(r"E\d{4}", code):
            kind = "rustc"
        elif code and code.startswith("clippy::"):
            kind = "clippy"
        elif code:
            kind = "rustc_lint"
        else:
            kind, code = _classify(None, children)
        d = Diagnostic(kind, code, msg["message"], normalize_path(span["file_name"]),
                       span["line_start"], span["column_start"])
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def parse_checkout(text):
    """{pr, head, base} of the PR merge commit actions/checkout built, if logged."""
    m = MERGE.search(text)
    if not m:
        return None
    pr = PR_REF.search(text)
    return {"pr": int(pr.group(1)) if pr else None, "head": m.group(1), "base": m.group(2)}
