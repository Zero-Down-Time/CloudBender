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
REGISTRY := <your-registry>
IMAGE := <image_name>
REGION := <AWS region of your registry>

include .ci/podman.mk
```

### 3. Integrate with Jenkins

Add a `Jenkinsfile` using the shared libraries:

```groovy
@Library('ci-tools-lib') _

// Just-based projects (recommended)
justContainer(
  buildOnly: ['src/.*', '.justfile'],
  needBuilder: true,
  imageName: 'my-app',
)

// Or Make-based projects (deprecated)
buildPodman(
  buildOnly: ['src/.*', 'Cargo.*'],
)
```

## Components

### Just — `.just` modules (recommended)

| Module            | Key Recipes                                              |
|-------------------|----------------------------------------------------------|
| `container.just`  | `build`, `scan`, `push`, `ecr-login`, `clean`, manifest management |
| `rust.just`       | `prepare`, `lint` (clippy + cargo-deny), `build`, `test`, version bumping |
| `git.just`        | Version computation from tags, `tag-push`, legacy tag cleanup |
| `builder.just`    | Builder container creation and execution via Buildah      |

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
| `make create-repo`    | Create AWS ECR public repository     |
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

- **`ecr_public_lifecycle.py`** — Python utility (requires `boto3`) to manage ECR image lifecycle: removes untagged images, prunes old dev-tagged images, keeps a configurable number of recent tagged images.
- **`utils.sh`** — Bash helpers for semantic version bumping (`bumpVersion`) and git commit/tag/push automation (`addCommitTagPush`).
- **`Dockerfile.rust`** — Multi-stage Rust builder image (Alpine 3.23) with cargo, clippy, sccache, cargo-auditable, cargo-deny, and just.

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
