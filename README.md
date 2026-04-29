# ci-tools-lib

Various toolchain bits and pieces shared between projects — a shared CI/CD toolchain library for building, testing, scanning, and publishing containerized applications using Podman, Jenkins, and AWS ECR.

## Features

- **Container Build Orchestration** — Podman-based rootless container builds with multi-architecture support (amd64, arm64)
- **Jenkins Shared Libraries** — Reusable pipeline templates for Just and Make-based projects
- **Gitea SCM Integration** — Native change detection via API for PR and commit changesets
- **AWS ECR Public** — Registry login, push, manifest management, and automated image lifecycle cleanup
- **Vulnerability Scanning** — Grype integration with configurable severity thresholds and JSON reporting
- **Semantic Versioning** — Automatic version computation from git tags with branch suffix support
- **Build Protection** — PR safety mechanism that overwrites build config files from the target branch
- **Builder Containers** — Optional isolated build environments (e.g. Rust toolchain with sccache, cargo-deny, cargo-auditable)

## Quickstart

### 1. Add as a git subtree

```bash
git subtree add --prefix .ci https://git.zero-downtime.net/ZeroDownTime/ci-tools-lib.git main --squash
```

### 2. Configure your project

**Using Just** (recommended) — Import the relevant `.just` modules in your `justfile`:

```just
import '.ci/container.just'
import '.ci/rust.just'
import '.ci/git.just'
```

**Using Make** (deprecated — support will be removed midterm) — Create a top-level `Makefile`:

```makefile
REGISTRY := public.ecr.aws/<alias>             # or 1234567890.dkr.ecr.<region>.amazonaws.com
IMAGE := <image_name>

include .ci/podman.mk
```

### 3. Integrate with Jenkins

Add a `Jenkinsfile` using the shared libraries:

```groovy
@Library('ci-tools-lib') _

// Just-based projects (recommended)
justContainer(
  imageName:   'my-app',
  registry:    'public.ecr.aws/<alias>',  // or '<account>.dkr.ecr.<region>.amazonaws.com'
  buildOnly:   ['src/.*', '.justfile'],
  needBuilder: true,
)

// Or Make-based projects (deprecated)
buildPodman(
  buildOnly: ['src/.*', 'Cargo.*'],
)
```

`registry` is required — set it explicitly per project. The library auto-detects public vs private ECR from the URL shape (`public.ecr.aws/...` vs `*.dkr.ecr.<region>.amazonaws.com`) and dispatches to the correct `aws ecr` / `aws ecr-public` API. Region for private is parsed from the hostname. Both the agent and dev workstation need ambient AWS credentials in scope (env vars, instance profile, etc.) — the library does no credential plumbing.

## Components

### Just — `.just` modules (recommended)

| Module            | Key Recipes                                              |
|-------------------|----------------------------------------------------------|
| `container.just`  | `build`, `scan`, `push`, `ecr-login`, `create-repo`, `clean`, manifest management. Public and private AWS ECR auto-detected; override default via `REGISTRY` env var. |
| `rust.just`       | `prepare`, `lint` (clippy + cargo-deny), `build`, `test`, version bumping |
| `git.just`        | Version computation from tags, `tag-push`, legacy tag cleanup |
| `builder.just`    | Builder container creation and execution via Buildah      |
| `common.just`     | `scan-src` source secret scan; imported by language modules |

### Make — `podman.mk` (deprecated — support will be removed midterm)

Common Makefile include providing standardized build targets:

| Target                | Description                          |
|-----------------------|--------------------------------------|
| `make help`           | Show available targets               |
| `make prepare`        | Custom pre-build preparation         |
| `make fmt`            | Auto-format source code              |
| `make lint`           | Lint source code                     |
| `make build`          | Build container image                |
| `make test`           | Test built artifacts                 |
| `make scan`           | Scan image with Grype                |
| `make push`           | Push image to registry               |
| `make ecr-login`      | Login to AWS ECR                     |
| `make rm-remote-untagged` | Cleanup untagged/dev images     |
| `make create-repo`    | Create AWS ECR (public or private) repository |
| `make clean`          | Clean up build artifacts             |
| `make ci-pull-upstream` | Pull latest `.ci` subtree          |

### Jenkins — Shared Libraries (`vars/`)

| Library                  | Purpose                                              |
|--------------------------|------------------------------------------------------|
| `justContainer.groovy`   | Full pipeline for Just-based container projects       |
| `buildPodman.groovy`     | Full pipeline for Make-based container projects (deprecated) |
| `gitea.groovy`           | Gitea API integration for change detection            |
| `protectBuildFiles.groovy` | Overwrites CI files from target branch during PR builds |

**Pipeline stages:** Prepare → Lint → Build → Test → Scan → Push → Cleanup

### Utilities

- **`ecr_lifecycle.py`** — Python utility (requires `boto3`) to manage ECR image lifecycle for public *and* private ECR: removes untagged images, prunes old dev-tagged images, keeps a configurable number of recent tagged images. Detects public vs private from the `--registry` URL.
- **`utils.sh`** — Bash helpers for semantic version bumping (`bumpVersion`) and git commit/tag/push automation (`addCommitTagPush`).
- **`Dockerfile.rust`** — Multi-stage Rust builder image (Alpine 3.23) with cargo, clippy, sccache, cargo-auditable, cargo-deny, and just.

## Monorepo layout

For a monorepo where each service has its own `.justfile`, `Jenkinsfile`, and `Dockerfile` under a subdirectory (e.g. `services/api-users/`), share one `.ci/` subtree at the repo root and pass per-service config:

```
repo/
├── .ci/                            # git subtree of ci-tools-lib
└── services/
    └── api-users/
        ├── Jenkinsfile
        ├── .justfile
        ├── Dockerfile
        └── pyproject.toml
```

**`services/api-users/.justfile`:**

```just
# Per-service tag prefix so `git describe` only sees this service's releases
export TAG_MATCH := "api-users/v*.*.*"

# Toolchain — flat-imported so `just lint`, `just prepare`, `just scan-src`,
# `just use-builder lint` etc. work. Pulls common.just, builder.just, git.just.
import '../../.ci/python.just'

# Container recipes namespaced — Jenkins glue calls `just container::build`.
mod container '../../.ci/container.just'
```

**`services/api-users/Jenkinsfile`:**

```groovy
@Library('ci-tools-lib') _

justContainer(
    workDir:     'services/api-users',
    imageName:   'api-users',
    registry:    '1234567890.dkr.ecr.us-east-1.amazonaws.com',  // or public.ecr.aws/<alias>
    buildOnly:   ['services/api-users/.*', '\\.ci/.*'],
    needBuilder: true,
)
```

`protect` defaults to `["${workDir}/.justfile", "${workDir}/Jenkinsfile", '.ci/**']`, so service-scoped build files are restored from the target branch on PR builds without needing to override it. Tag releases as `api-users/v1.2.3` and configure the Jenkins multibranch project's *Script Path* to `services/*/Jenkinsfile`.

## Local dev

`registry` from the Jenkinsfile becomes the `REGISTRY` env var inside the just recipes. For matching local behaviour, set the same env var in your shell:

```bash
export REGISTRY=1234567890.dkr.ecr.us-east-1.amazonaws.com   # or public.ecr.aws/<alias>
just container::build my-app
just container::push my-app
```

Or set it once in the consuming project's justfile:

```just
export REGISTRY := "1234567890.dkr.ecr.us-east-1.amazonaws.com"
```

To create a new repository (run once, requires AWS create permissions):

```bash
REGISTRY=... just container::create-repo my-app
```

`build`, `scan`, and `clean` recipes don't reference `REGISTRY`, so it's only required when you actually push or manage remote images.

## Maintenance

Pull the latest upstream changes into your project:

```bash
git subtree pull --prefix .ci https://git.zero-downtime.net/ZeroDownTime/ci-tools-lib.git main --squash
```

## Renovate

Run renovate locally to test custom config:

```bash
LOG_LEVEL=debug ~/node_modules/renovate/dist/renovate.js --platform local --dry-run
```

## License

[GNU AGPL v3](LICENSE)
