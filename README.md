# ![Logo](https://git.zero-downtime.net/ZeroDownTime/CloudBender/media/branch/main/cloudbender.png) CloudBender

## About

CloudBender is an Infrastructure-as-Code orchestration tool for deploying and maintaining AWS infrastructure in an automated, traceable, and version-controlled manner.

It provides a unified CLI with first-class support for two IaC backends:
- [Pulumi](https://www.pulumi.com/docs/) — Python-based IaC with S3-backed state (no Pulumi Cloud dependency)
- [AWS CloudFormation](https://aws.amazon.com/cloudformation) — with Jinja2 template rendering

### Key Features

- **Dual IaC backend** — mix Pulumi and CloudFormation stacks in the same project
- **Hierarchical configuration** — deep-merging config inheritance ideal for multi-account, multi-region AWS environments
- **Self-hosted state** — Pulumi state stored in S3 per-account/per-region; no data sent to external APIs
- **Secrets management** — Pulumi native encryption and [SOPS](https://github.com/mozilla/sops) for CloudFormation
- **Dependency resolution** — automatic stack ordering based on declared dependencies
- **Lifecycle hooks** — pre/post create and update hooks for custom automation
- **Drift detection** — `refresh` command for Pulumi stacks
- **Auto-generated docs** — markdown documentation from stack metadata and outputs

## Requirements

- Python >= 3.12
- [Pulumi](https://www.pulumi.com/docs/get-started/install/) >= 3.x
- `podman` or `docker` (for container-based tasks)
- AWS credentials configured via profiles (`~/.aws/config`)

## Installation

### Containerized (recommended)

The preferred way to run CloudBender is via the public container image. This ensures all tools and dependencies are in sync and tested.

> **Note:** Requires Linux with kernel >= 5.12, Cgroups V2, and podman for rootless nested containers.

Verify your setup supports nested containers:

```
podman run --rm -v .:/workspace -v $HOME/.aws/config:/workspace/.aws/config \
  public.ecr.aws/zero-downtime/cloudbender:latest \
  podman run -q --rm docker.io/busybox:latest echo "Rootless container inception works!"
```

If successful, add an alias to your shell profile:

```bash
alias cloudbender="podman run --rm -v .:/workspace \
  -v $HOME/.aws/config:/home/cloudbender/.aws/config \
  public.ecr.aws/zero-downtime/cloudbender:latest cloudbender"
```

### Local install

```bash
pip3 install -U cloudbender
curl -fsSL https://get.pulumi.com | sh
```

### Verify installation

```
cloudbender version
```

Expected output:
```
CloudBender: 0.x.x
Pulumi: v3.228.0
Podman/Docker: podman version 5.x.x
```

## Project Layout

A CloudBender project follows this directory structure:

```
my-project/
├── config/                        # Configuration tree
│   ├── config.yaml                # Global settings (profile, region, options)
│   ├── production/                # Stack group
│   │   ├── config.yaml            # Group-level overrides (deep-merged with parent)
│   │   ├── us-east-1/             # Nested stack group (e.g. per-region)
│   │   │   ├── config.yaml        # Region-level overrides
│   │   │   └── vpc.yaml           # Stack definition
│   │   └── networking.yaml        # Stack definition
│   └── staging/
│       ├── config.yaml
│       └── app.yaml
├── cloudformation/                # CloudFormation Jinja2 templates
│   └── vpc.yaml.jinja
└── artifacts/                     # Artifacts (Pulumi programs, scripts, etc.)
    └── pulumi/
        └── vpc.py                 # Pulumi Python program
```

### Configuration Hierarchy

Configuration is hierarchically merged from parent to child. Lower-level config files override higher-level values, with deep merging for dictionaries and arrays. This enables DRY configuration across accounts, regions, and environments.

### Stack Modes

Each stack operates in one of three modes:

| Mode | Description |
|---|---|
| `CloudBender` (default) | CloudFormation with Jinja2 rendering |
| `pulumi` | Pulumi Python IaC |
| `Piped` | CloudFormation with inter-stack reference injection |

## CLI Reference

```
Usage: cloudbender [OPTIONS] COMMAND [ARGS]...

Options:
  --profile TEXT  Use named AWS .config profile, overwrites any stack config
  --region TEXT   Use region, overwrites any stack config
  --dir TEXT      Specify cloudbender project directory.
  --debug         Turn on debug logging.
  --help          Show this message and exit.
```

### Core Operations

| Command | Description |
|---|---|
| `provision <stack\|group> [--multi]` | Create or update stacks/stack groups |
| `delete <stack\|group> [--multi]` | Delete stacks/stack groups (reverse dependency order) |
| `preview <stack>` | Preview Pulumi stack changes before applying |
| `refresh <stack>` | Drift detection — refreshes Pulumi stack state against actual cloud resources |

### CloudFormation Commands

| Command | Description |
|---|---|
| `render <stack> [--multi]` | Render Jinja2 templates to CloudFormation YAML |
| `validate <stack> [--multi]` | Validate rendered templates using `cfn-lint` |
| `create-change-set <stack> <name>` | Create a CloudFormation change set |
| `sync <stack> [--multi]` | Render + provision in a single step |

### Configuration & Secrets

| Command | Description |
|---|---|
| `get-config <stack> <key>` | Retrieve a config value (decrypted if secret) |
| `set-config <stack> <key> <value> [--secret]` | Store a config value (encrypted if `--secret`) |

### Inspection & Documentation

| Command | Description |
|---|---|
| `outputs <stack> [--include regex] [--values]` | Print stack outputs, optionally filtered |
| `docs <stack> [--multi]` | Generate documentation for stacks |
| `list-stacks <group>` | List all Pulumi stacks in a group |
| `version` | Display CloudBender, Pulumi, and Podman/Docker versions |

### Pulumi State Management

| Command | Description |
|---|---|
| `export <stack> [-r]` | Export Pulumi stack state (optionally remove pending operations) |
| `import <stack> <file>` | Import a Pulumi state file |
| `assimilate <stack>` | Import existing AWS resources into a Pulumi stack |
| `execute <stack> [function] [args]` | Run custom Python functions within a stack context |

### Utility

| Command | Description |
|---|---|
| `wrap <group> <cmd>` | Execute an external program with stack group context |
| `clean` | Delete all previously rendered template files |

## Architecture

### State Management

**Pulumi** — State is stored in S3 within your own AWS account, in the same region as the deployed resources. No data is shared with Pulumi Cloud APIs. CloudBender creates temporary, isolated workspaces per stack operation and injects configuration (account ID, region, parameters) automatically.

**CloudFormation** — State is managed natively by the AWS CloudFormation service. Templates can optionally be stored in S3 via the `template_bucket_url` setting.

### Secrets

**Pulumi** — Uses native Pulumi secret handling with passphrase-based or custom encryption keys. See [Pulumi Secrets docs](https://www.pulumi.com/docs/intro/concepts/secrets/).

**CloudFormation** — Supports [SOPS](https://github.com/mozilla/sops) for encrypted config files. Encrypted files are automatically detected and decrypted at runtime. All required decryption metadata must be embedded in the SOPS config or set via environment variables. SOPS support can be disabled by setting the `DISABLE_SOPS` environment variable.

### Hooks

Stacks support lifecycle hooks defined in artifact metadata:

- `pre_create`, `post_create` — before/after stack creation
- `pre_update`, `post_update` — before/after stack update

Built-in hook type:
- `cmd` — execute arbitrary shell commands via subprocess

### Dependency Resolution

Stacks can declare dependencies on other stacks. CloudBender resolves these into a dependency graph and provisions stacks in the correct order, parallelizing independent stacks where possible (CloudFormation stacks run in parallel; Pulumi stacks run sequentially due to thread-safety constraints).

## Environment Variables

| Variable | Description |
|---|---|
| `CLOUDBENDER_PROJECT_ROOT` | Override the project root directory |
| `DISABLE_SOPS` | Disable SOPS decryption for config files |
| `PULUMI_SKIP_UPDATE_CHECK` | Set automatically in the container image |

## Development

```bash
# Install dependencies
just prepare

# Format code
just fmt

# Lint
just lint

# Run tests
just test

# Build distribution
just build
```

## License

[AGPL-3.0-or-later](LICENSE.md)

## Links

- **Homepage:** https://git.zero-downtime.net/ZeroDownTime/CloudBender
- **Container image:** `public.ecr.aws/zero-downtime/cloudbender:latest`
- **PyPI:** `pip install cloudbender`
