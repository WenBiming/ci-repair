"""Project-specific settings. Everything that would change for another Rust project lives here."""

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class StepRule:
    """Maps a failed CI step to the `cargo check` arguments that reproduce its compile stage."""
    pattern: re.Pattern
    cargo_args: tuple | None  # None: step cannot be reproduced (yet)


@dataclass(frozen=True)
class Project:
    owner: str
    repo: str
    # Workflow files whose jobs compile the project (bots, publishing, benchmarks excluded).
    workflows: tuple
    step_rules: tuple
    default_cargo_args: tuple
    # Jobs whose feature set must be excluded (e.g. non-open-source code).
    excluded_job: re.Pattern | None = None
    rustflags: str = "-D warnings"
    env: dict = field(default_factory=dict)

    @property
    def slug(self):
        return f"{self.owner}/{self.repo}"

    def cargo_args_for_step(self, step_name):
        for rule in self.step_rules:
            if rule.pattern.search(step_name or ""):
                return rule.cargo_args
        return self.default_cargo_args


MEILISEARCH = Project(
    owner="meilisearch",
    repo="meilisearch",
    workflows=("test-suite.yml", "check-openapi-file.yml"),
    step_rules=(
        StepRule(re.compile(r"without any default features", re.I),
                 ("--workspace", "--no-default-features")),
        # `cargo xtask list-features` picks features at CI time; not reproduced yet.
        StepRule(re.compile(r"almost all features", re.I), None),
        StepRule(re.compile(r"cargo test|clippy", re.I), ("--workspace", "--all-targets")),
        StepRule(re.compile(r"^build$|cargo build", re.I), ("--workspace",)),
        # check-openapi-file.yml: `cargo run --release -p openapi-generator -- ...`
        StepRule(re.compile(r"openapi|^check ", re.I), ("--workspace",)),
    ),
    default_cargo_args=("--workspace", "--all-targets"),
    # Enterprise Edition code is under BUSL-1.1, not an open-source licence.
    excluded_job=re.compile(r"--features enterprise|enterprise", re.I),
    env={"RUST_BACKTRACE": "1",
         # lindera build scripts download dictionaries; cache them in the assets volume.
         "LINDERA_BUILD_DICTIONARY_CACHE_DIR": "/assets/lindera"},
)

PROJECTS = {"meilisearch": MEILISEARCH}
