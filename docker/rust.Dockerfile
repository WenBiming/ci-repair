# Build environment for reproducing Rust CI compile failures.
# One image per toolchain version: docker build --build-arg RUST_VERSION=1.98.1 ...
ARG RUST_VERSION
FROM rust:${RUST_VERSION}-bookworm

# rust-toolchain.toml may request components; install them now so builds can run offline.
RUN rustup component add clippy rustfmt

# Source tree is bind-mounted here; dependencies and build output live in named volumes.
ENV CARGO_TARGET_DIR=/target \
    CARGO_TERM_COLOR=never \
    CARGO_INCREMENTAL=0
WORKDIR /src
