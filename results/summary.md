# CI compilation-failure screening (last 90 days)

Est. compile failures = sampled share of failed runs whose log shows a compiler error x total failed CI runs. '6-mo est.' scales by 2.0.

| Repo | Langs | Runs | Fail % | PR fail % | Compile share of failures (n) | Est. compile failures | 6-mo est. | Compile fail % of runs |
|---|---|---|---|---|---|---|---|---|
| tauri-apps/tauri | Rust | 14855 | 11.0% | 12.2% | 76.9% (13) | 1261 | 2522 | 8.5% |
| curl/curl | C | 44850 | 7.3% | 9.7% | 31.6% (19) | 1029 | 2058 | 2.3% |
| astral-sh/ruff | Rust | 34516 | 5.6% | 5.7% | 50.0% (20) | 974 | 1948 | 2.8% |
| microsoft/TypeScript | Go, TypeScript | 2784 | 19.1% | 28.6% | 36.4% (11) | 193 | 386 | 6.9% |
| angular/angular | TypeScript | 23801 | 4.8% | 31.2% | 11.8% (17) | 133 | 266 | 0.6% |
| go-gitea/gitea | Go | 35594 | 2.5% | 4.2% | 15.0% (20) | 132 | 264 | 0.4% |
| meilisearch/meilisearch | Rust | 1263 | 36.9% | 41.6% | 26.3% (19) | 123 | 246 | 9.7% |
| hashicorp/terraform | Go | 7385 | 13.1% | 11.8% | 11.1% (18) | 107 | 214 | 1.5% |
| rust-lang/rust-analyzer | Rust | 5466 | 7.3% | 11.0% | 25.0% (20) | 100 | 200 | 1.8% |
| traefik/traefik | Go | 6651 | 12.6% | 14.1% | 8.3% (12) | 70 | 140 | 1.1% |
| nushell/nushell | Rust | 3313 | 10.4% | 10.9% | 15.0% (20) | 52 | 104 | 1.6% |
| vitejs/vite | TypeScript | 7035 | 12.6% | 22.5% | 5.0% (20) | 44 | 88 | 0.6% |
| starship/starship | Rust | 2021 | 19.7% | 21.2% | 6.2% (16) | 25 | 50 | 1.2% |
| gohugoio/hugo | Go | 964 | 17.8% | 18.8% | 13.3% (15) | 23 | 46 | 2.4% |
| helix-editor/helix | Rust, Tree-sitter Query | 637 | 24.3% | 28.3% | 12.5% (16) | 19 | 38 | 3.0% |
| tokio-rs/tokio | Rust | 3352 | 5.6% | 6.4% | 5.0% (20) | 9 | 18 | 0.3% |
| apache/dubbo | Java | 220 | 23.2% | 25.7% | 5.0% (20) | 3 | 6 | 1.2% |
| astral-sh/uv | Rust | 11514 | 12.9% | 14.4% | 0.0% (20) | 0 | 0 | 0.0% |
| bevyengine/bevy | Rust | 18272 | 10.7% | 6.9% | 0.0% (19) | 0 | 0 | 0.0% |
| prometheus/prometheus | Go, TypeScript | 4763 | 18.9% | 35.9% | 0.0% (18) | 0 | 0 | 0.0% |
| etcd-io/etcd | Go | 3165 | 5.8% | 10.6% | 0.0% (13) | 0 | 0 | 0.0% |
| cli/cli | Go | 2149 | 7.0% | 9.6% | 0.0% (10) | 0 | 0 | 0.0% |
| apache/commons-lang | Java | 300 | 8.7% | 19.8% | 0.0% (11) | 0 | 0 | 0.0% |
| spring-projects/spring-boot | Java | 1844 | 16.9% | 31.0% | 0.0% (13) | 0 | 0 | 0.0% |
| alibaba/nacos | Java | 2254 | 14.4% | 24.5% |  (0) |  |  |  |
| redis/redis | C, Tcl | 6772 | 12.3% | 12.4% | 0.0% (12) | 0 | 0 | 0.0% |
| neovim/neovim | Vim Script, Lua, C | 41553 | 5.6% | 6.4% | 0.0% (13) | 0 | 0 | 0.0% |
