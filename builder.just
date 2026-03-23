set dotenv-load

update-builder target="rust":
  podman build -t local-podman-builder -f .ci/Dockerfile.{{ target }}

_builder:
  #!/usr/bin/env sh
  builder=$(buildah containers --json | jq -r '.[] | select(.imagename == "localhost/local-podman-builder:latest") | .id' 2>/dev/null)
  [ -z "$builder" -o "$builder" == "null" ] && builder=$(buildah from localhost/local-podman-builder:latest)
  echo $builder > .ci/.builder.id

# runs available just targets inside the builder
[no-exit-message]
use-builder +target: _builder
  buildah run -v $(pwd):/app $(cat .ci/.builder.id) -- just {{ target }}
