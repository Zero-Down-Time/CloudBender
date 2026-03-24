set allow-duplicate-variables
set dotenv-load

toolchain := "rust"

update-builder:
  podman build -t local-{{ toolchain }}-builder -f .ci/Dockerfile.{{ toolchain }}

# runs available just targets inside the builder
[no-exit-message]
use-builder +target:
  #!/usr/bin/env bash
  set -eo pipefail
  builder=$(buildah containers --json | jq -r '.[] | select(.imagename == "localhost/local-{{ toolchain }}-builder:latest") | .id' 2>/dev/null)
  [ -z "$builder" -o "$builder" == "null" ] && builder=$(buildah from localhost/local-{{ toolchain }}-builder:latest)
  trap "buildah rm $builder > /dev/null" EXIT
  buildah run -v $(pwd):/app $builder -- just {{ target }}
