# ci-tools-lib

Shared CI/CD toolchain for ZeroDownTime: rootless Podman container builds, multi-arch (amd64/arm64), Grype + betterleaks scanning, AWS ECR Public publishing. Consumed via `git subtree` as `.ci/` inside downstream projects, plus a Jenkins shared-library reference (`@Library('ci-tools-lib')`).

## Core design rule

**All build logic lives in `*.just` modules. The Jenkinsfile and `vars/*.groovy` are glue only.**

Developers must reproduce full CI behaviour locally by running the same `just` recipes Jenkins runs. If logic leaks into Groovy, dev and CI diverge and "works on my machine" stops being meaningful.

- Acceptable in Groovy: `pipeline { ... }` declarative blocks, agent labels, `dir()`, `stash`/`unstash`, `withEnv`, `withCredentials`, `recordIssues`, `httpRequest`, `readJSON`/`writeJSON`, `currentBuild.description` flags, `env.*` reads.
- Not acceptable in Groovy: invoking scanners/linters/builders directly, computing versions, tagging/pushing images, rootfs extraction, file manipulation a developer would also need.
- A `sh` call in a Groovy wrapper should look like `sh "just <target> '${arg}'"` — nothing more.

## Architecture

Two layers:

1. **Just modules** (`*.just` at repo root, copied into consumers as `.ci/*.just`) — actual build logic. Composable via `import 'foo.just'`.
2. **Jenkins shared library** (`vars/*.groovy`) — thin per-stage wrappers + Jenkins-only concerns (changeset detection, recordIssues, PR build-file protection).

**Pipeline (declarative, in `vars/justContainer.groovy`):** Prepare → Lint → Build → Test → Scan → Push → Cleanup.

- `currentBuild.description == 'SKIP'` is the cross-stage "no source changes, skip downstream" signal, set by `containerBuild` when no changed files match `buildOnly` patterns.
- `Push` stage additionally gated by `not { changeRequest() }` — PRs never push.

## Files

### Just modules

| File | Purpose |
|------|---------|
| `git.just` | `git_tag` / `git_branch` / `git_repo_name` derivation, `tag` with sanitized branch suffix when not on main/master, arch validation (amd64/arm64), `_addCommitTagPush`, `cleanup-tags`, `ci-pull-upstream` |
| `common.just` | `scan-src` (source betterleaks). Imported by language modules so every language toolchain gets it. |
| `container.just` | `build`, `scan` (image betterleaks + grype), `ecr-login`, `push` (multi-arch manifest), `clean`, `rm-remote-untagged`, `create-repo`. Public and private AWS ECR are auto-detected from the `REGISTRY` env var. **`REGISTRY` is required** — no default. `build`/`scan`/`clean` don't reference it (lazy eval), so they work without it; push/login/create-repo/rm-remote-untagged fail with "environment variable `REGISTRY` not present" if unset. |
| `builder.just` | `update-builder` (build toolchain image), `use-builder <target>` (run target inside toolchain container via `buildah from` + `buildah run -v $(pwd):/app`) |
| `rust.just` | imports `common.just`; `prepare` (cargo fetch), `lint` (clippy + cargo-deny), `build [release]` (cargo auditable), `test`, `cut-release` |
| `python.just` | imports `common.just`; uv-based: `prepare` (uv sync --locked), `lint` (flake8), `build` (uv build), `test` (uv run pytest), `upload` (uv publish) |

### Jenkins shared library (`vars/`)

| File | Purpose |
|------|---------|
| `justContainer.groovy` | **Current entry point** — declarative pipeline composing per-stage helpers |
| `containerPrepare.groovy` | `gitea.getChangeset()` → stash, `protectBuildFiles`, optional `just prepare` |
| `containerLint.groovy` | `just scan-src` (source secrets, gated on `just --summary` containing it) + recordIssues, then `just lint` (or `just use-builder lint`) |
| `containerBuild.groovy` | unstash changeset, gate on `pathsChanged(buildOnly)` or `forceBuild`, run `just container::build`. Sets `currentBuild.description = 'SKIP'` when nothing matches |
| `containerTest.groovy` | **Stub** — currently `sh "echo"`. Intended `just container::test` is commented out |
| `containerScan.groovy` | `just container::scan` with env vars for SARIF/JSON output paths, then `recordIssues` for grype CVEs and image leaks |
| `containerPush.groovy` | `just container::push` + `just container::rm-remote-untagged`. Optional `registry` config is propagated as `REGISTRY` env var via `withEnv` so private ECR is selected from the consumer Jenkinsfile without modifying their justfile. |
| `containerClean.groovy` | `just container::clean` |
| `gitea.groovy` | Gitea REST client: parses `GIT_URL`, fetches PR files (`/pulls/N/files`) or commit diff (`/compare/base...head`); `pathsChanged(files, patterns)` regex-matches |
| `protectBuildFiles.groovy` | On PRs only: `git checkout origin/<target> -- <files>` to overwrite CI files from target branch (defends against PRs modifying their own CI) |
| `quietCheckout.groovy` | Manual `git fetch/checkout --quiet` replicating Git plugin behaviour with less console noise |
| `buildPodman.groovy` | **Deprecated** — Make-based equivalent of `justContainer`, kept for unmigrated projects |

### Other

- `Dockerfile.rust`, `Dockerfile.python` — Alpine 3.23 toolchain images for `use-builder` flow.
- `podman.mk` — **deprecated** Make include (feature-parallel to Just modules; uses `::` recipes for extensibility).
- `ecr_lifecycle.py` — boto3 image cleanup for public *and* private ECR. Public/private dispatch and region are derived from the `--registry` URL. `--dev` deletes images whose every tag matches `*-g<hash>` or contains `dirty`.
- `utils.sh` — Bash `bumpVersion` (semver awk) + `addCommitTagPush`.

## Conventions

### Shell escaping in Groovy `sh` calls

Always single-quote interpolated values: `sh "just <target> '${var}'"`. Use `withEnv` for environment variables instead of inline `KEY=VAL ...` so Jenkins sets them directly without shell parsing. Values come from the Jenkinsfile config map (controlled), so single-quote is safe; if a value could contain a `'`, escape it explicitly.

**Optional positional args must be omitted, not passed as `''`.** Just recipes typically default an arg like `image=git_repo_name`; passing an empty string from Groovy overrides that default with empty and breaks the recipe. Pattern:

```groovy
def imageArg = imageName ? " '${imageName}'" : ''
sh "just container::build${imageArg}"
```

### Optional just recipes

Stages call recipes conditionally so consumers don't need to define every target:

```
sh "if just --summary | grep -q lint; then just lint; fi"
```

### Builder container indirection

`needBuilder: true` in the config makes Prepare/Lint/Build run targets inside `local-{toolchain}-builder` via `just use-builder <target>`. The agent only needs Podman/Buildah, not language toolchains.

### Multi-arch

Per-arch images tagged `<image>:<tag>-<arch>`, then `push` builds a manifest list referencing both archs. `all_archs := "amd64 arm64"` in `git.just`.

### Versioning

`git_tag` from `git describe --tags --match "${TAG_MATCH:-v*.*.*}" --dirty`, falls back to short SHA. The default match `v*.*.*` covers single-repo projects; monorepo services override it via `export TAG_MATCH := "<service>/v*.*.*"` in their justfile so `git describe` only considers that service's release tags. On non-main branches, `tag` becomes `<git_tag>-<sanitized-branch>` unless the branch is already a substring/equal.

### Monorepo support

The library is path-aware so a single `.ci/` subtree at the repo root serves multiple services in subdirectories:

- `builder.just` resolves `Dockerfile.<toolchain>` via `source_directory()` so `update-builder` works regardless of caller cwd.
- `use-builder` mounts the repo root (not `$(pwd)`) and `cd`s to the caller's relative path inside the container, so `import '../../.ci/<lang>.just'` from a service justfile resolves at runtime.
- `git.just` honours `$TAG_MATCH` for per-service tag prefixes.
- `containerPrepare` defaults `protect` to `["${workDir}/.justfile", "${workDir}/Jenkinsfile", '.ci/**']` — service-scoped without needing per-Jenkinsfile config. Note: protecting `Jenkinsfile` is symbolic only; Jenkins reads it before any pipeline step runs, so `protectBuildFiles` cannot prevent a malicious PR Jenkinsfile from executing. Real defense lives in Jenkins controller config (PR approval policies for external contributors).

Per-service Jenkinsfile typically sets `workDir`, `imageName`, `buildOnly` (regex with the service's path prefix), and `needBuilder`. See README "Monorepo layout" for a full example.

## External integration

- **Gitea** — changeset detection via REST API. Credentials: Jenkins username/password credential ID `gitea-jenkins-password` (default, configurable). API base derived from `env.GIT_URL` or overridden via config.
- **AWS ECR (public or private)** — `aws ecr-public get-login-password` (region `us-east-1`, fixed for ECR Public) or `aws ecr get-login-password` (region parsed from the registry hostname `*.dkr.ecr.<region>.amazonaws.com`) piped to `podman login`. **No default registry** — every consumer must set `REGISTRY` (commonly via `registry:` in the Jenkinsfile config, which `containerPush.groovy` propagates as the `REGISTRY` env var via `withEnv`). Local dev and Jenkins agent both rely on ambient AWS credentials (env vars, instance profile, etc.) — no credential plumbing in the library.
- **Required Jenkins plugins:** Pipeline (declarative), Git, HTTP Request, Pipeline Utility Steps, Credentials Plugin, Warnings Next Generation (`recordIssues` with `grype` and `sarif` tools).

## Known gaps

- **`containerTest.groovy` is a stub.** Test stage runs `sh "echo"` and does nothing else. The Make path (`buildPodman.groovy`) does run `make test`.
- **No tests for the library itself.** No Jenkinsfile linter integration. Correctness verified by running against real consumer projects.

## Working with this repo

- Edits to `vars/*.groovy` only affect Jenkins consumers when the library is reloaded (controller-side cache).
- Edits to `*.just` propagate to consumers when they `git subtree pull` the new revision (or run their `ci-pull-upstream` recipe).
- Don't add a recipe to a `*.just` module without considering whether developers will actually run it locally; if it's Jenkins-only, it belongs in Groovy.
- Don't add `sh` logic to Groovy beyond invoking `just`. If the temptation arises, the right move is a new just recipe.
