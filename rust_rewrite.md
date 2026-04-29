# CloudBender — Rust Rewrite Recommendations

## Why Rust?

- **Single binary distribution** — eliminates Python runtime, Pulumi CLI, and UV dependency chain; simplifies the container image drastically
- **Performance** — parallel stack operations become trivial with Rust's concurrency model (tokio/rayon); no GIL limitations
- **Type safety** — the current codebase relies heavily on deeply-nested YAML dicts passed through `dict_merge`; Rust structs would catch config errors at compile time
- **Smaller container image** — replace the current Alpine + Python + Pulumi + AWS CLI image with a single static binary

---

## Phased Migration Plan

### Phase 1 — Core data model & config system
- [ ] Define Rust structs for `Stack`, `StackGroup`, and `CloudBender` (mirrors `stack.py`, `stackgroup.py`, `core.py`)
- [ ] Implement hierarchical YAML config loading with deep-merge semantics (replace `utils.dict_merge`)
- [ ] Use `serde` + `serde_yaml` for config deserialization with strong typing
- [ ] Add validation at deserialization time (region, profile, mode enum, required fields)
- [ ] Port `exceptions.py` to Rust error types with `thiserror`

### Phase 2 — AWS integration
- [ ] Replace `boto3`/`BotoConnection` with the `aws-sdk-rust` crate (`aws-sdk-cloudformation`, `aws-sdk-s3`, `aws-sdk-sts`)
- [ ] Implement AWS profile + MFA + SSO credential resolution (currently `connection.py`)
- [ ] Port CloudFormation CRUD operations: create/update/delete stack, change sets, wait loops
- [ ] Port S3 template upload and state storage logic

### Phase 3 — Templating engine
- [ ] Replace Jinja2 with `tera` or `minijinja` (Rust template engines with similar syntax)
- [ ] Port custom Jinja filters: `option()`, `include_raw_gz()`, `regex_replace`, `toyaml`
- [ ] Implement CloudFormation template rendering pipeline
- [ ] Port `cfn-lint` validation (call external `cfn-lint` or implement key checks natively)

### Phase 4 — Pulumi integration
- [ ] Use Pulumi Automation API via its gRPC/REST interface instead of Python subprocess calls
- [ ] Re-implement `pulumi.py` workspace management (`@pulumi_ws` decorator logic)
- [ ] Handle S3-backed state backend configuration
- [ ] Port secrets/passphrase management
- [ ] Implement `resolve_outputs()` for cross-stack references

### Phase 5 — CLI
- [ ] Replace Click with `clap` (derive-based CLI parsing)
- [ ] Mirror all existing commands: `provision`, `delete`, `preview`, `refresh`, `render`, `validate`, `sync`, `outputs`, `docs`, `export`, `import`, `assimilate`, `execute`, `wrap`, `clean`, `list-stacks`, `version`
- [ ] Implement `--multi` parallel execution using `tokio::spawn` or `rayon`
- [ ] Replace `Rich` terminal output with `indicatif` (progress bars) + `console` (colors/styling)
- [ ] Port `--profile`, `--region`, `--dir`, `--debug` global options

### Phase 6 — Hooks & lifecycle
- [ ] Port `hooks.py` lifecycle hook system (`pre_create`, `post_create`, `pre_update`, `post_update`)
- [ ] Support shell command hooks via `tokio::process::Command`
- [ ] Implement the `@exec_hooks` decorator pattern as a trait or middleware

### Phase 7 — Dependency resolution & orchestration
- [ ] Port topological sort for stack dependency ordering
- [ ] Implement parallel CloudFormation stack provisioning (safe — use `tokio` tasks)
- [ ] Keep Pulumi stacks sequential (or explore if Automation API supports parallel in Rust)
- [ ] Add proper cancellation and graceful shutdown (Ctrl+C handling via `tokio::signal`)

### Phase 8 — Docs, secrets & utilities
- [ ] Port markdown doc generation (`templates/stack-doc.md`) using `tera`
- [ ] Implement SOPS integration for CloudFormation secret decryption (shell out to `sops` binary or use a Rust SOPS library)
- [ ] Port `get_docker_version()` / Podman version detection
- [ ] Implement `ensure_dir()` and logging setup (`tracing` crate)

---

## Recommended Crate Map

| Python component | Rust crate |
|---|---|
| `click` | `clap` (derive) |
| `boto3` | `aws-sdk-cloudformation`, `aws-sdk-s3`, `aws-sdk-sts` |
| `ruamel.yaml` | `serde_yaml` |
| `jinja2` | `minijinja` or `tera` |
| `rich` | `indicatif` + `console` |
| `pexpect` (Pulumi CLI) | `tokio::process` or Pulumi Automation API gRPC |
| `python-minifier` | `minify-js` (if needed) or drop |
| `cfn-lint` | shell out to `cfn-lint`, or rewrite key checks |
| logging | `tracing` + `tracing-subscriber` |
| `pathlib` | `std::path` + `walkdir` |
| `hashlib` | `sha2` crate |
| threading | `tokio` (async) or `rayon` (data parallelism) |
| `zipfile` / `gzip` | `flate2` |

---

## Architecture Recommendations

### Project structure
```
cloudbender-rs/
  Cargo.toml
  src/
    main.rs            # CLI entry point
    cli/
      mod.rs           # clap command definitions
      provision.rs
      render.rs
      ...
    core/
      mod.rs           # CloudBender orchestrator
      stack.rs          # Stack struct & operations
      stackgroup.rs     # StackGroup + config merging
      config.rs         # Config deserialization types
    aws/
      mod.rs
      connection.rs    # AWS SDK session management
      cloudformation.rs
      s3.rs
    template/
      mod.rs           # Tera/minijinja rendering
      filters.rs       # Custom template filters
    pulumi/
      mod.rs           # Automation API integration
      state.rs         # S3 state backend
    hooks.rs           # Lifecycle hooks
    errors.rs          # Error types
    utils.rs           # Deep merge, logging, etc.
```

### Key design decisions
1. **Use `async` throughout** — AWS SDK for Rust is async-native; lean into `tokio`
2. **Strong config types** — use `serde` enums for `mode` (CloudBender | Pulumi | Piped) instead of string matching
3. **Builder pattern** for `Stack` construction — replaces the mutable attribute-setting in Python's `__init__`
4. **Trait-based hooks** — define a `Hookable` trait instead of Python decorators
5. **Feature flags** — gate Pulumi support behind a Cargo feature so users who only need CloudFormation get a smaller binary

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Jinja2 template compatibility | `minijinja` is designed to be Jinja2-compatible; test against existing templates early |
| Pulumi Automation API in Rust | Pulumi's Automation API is primarily Go/Node/Python; may need to shell out to `pulumi` CLI as fallback |
| SOPS integration | Continue shelling out to `sops` binary; no mature Rust SOPS library exists |
| Migration disruption | Keep the Python version maintained during transition; run both side-by-side with integration tests comparing outputs |
| Custom Jinja filters | Port `include_raw_gz` and `option` filters manually; they are small and well-defined |

---

## Quick Wins (start here)

1. **Config loader** — the `serde_yaml` deserialization with deep-merge is self-contained and immediately testable
2. **CLI skeleton** — `clap` derive gives you a working CLI in an afternoon
3. **Template rendering** — `minijinja` can validate against existing rendered CloudFormation output for correctness
4. **`dict_merge` in Rust** — the recursive merge logic in `utils.py` is a great first function to port and unit-test
