set allow-duplicate-variables
set dotenv-load

toolchain := "rust"

update-builder:
  podman build -t local-{{ toolchain }}-builder -f {{ source_directory() }}/Dockerfile.{{ toolchain }} {{ source_directory() }}

# runs available just targets inside the builder.
# Mounts the repo root so relative imports like `../../.ci/python.just` resolve,
# then cd's into the caller's path within the repo.
[no-exit-message]
use-builder +target:
  #!/usr/bin/env bash
  root=$(git rev-parse --show-toplevel)
  cwd=$(pwd)
  case "$cwd" in
    "$root") rel="." ;;
    "$root/"*) rel="${cwd#"$root/"}" ;;
    *) echo "cwd $cwd not under repo root $root" >&2; exit 1 ;;
  esac
  builder=$(buildah containers --json | jq -r '.[] | select(.imagename == "localhost/local-{{ toolchain }}-builder:latest") | .id' 2>/dev/null)
  [ -z "$builder" -o "$builder" == "null" ] && builder=$(buildah from localhost/local-{{ toolchain }}-builder:latest)
  trap "buildah rm $builder > /dev/null" EXIT
  buildah config --workingdir "/app/$rel" $builder
  buildah run -v "$root:/app" $builder -- just {{ target }}
