import 'git.just'

# Target ECR registry. Required: must be set by each consuming project as the
# REGISTRY env var (commonly via Jenkinsfile config or the consumer's justfile).
# Public ECR (public.ecr.aws/...) and private ECR (*.dkr.ecr.<region>.amazonaws.com)
# are auto-detected from the URL shape. Lazy: build/scan/clean recipes don't need it.
registry := env_var("REGISTRY")

[no-cd]
build image=git_repo_name:
  podman build --rm --layers -t {{ image }}:{{ git_tag }}-{{ arch }} --build-arg TAG={{ git_tag }} --build-arg ARCH={{ arch }} --platform linux/{{ arch }} .

# Scan image for secrets and vulnerabilities
[no-cd]
scan tmp="." image=git_repo_name:
  #!/usr/bin/env bash
  set -eo pipefail
  echo "Extracting rootfs of {{ image }}:{{ git_tag }}-{{ arch }} ..."
  _cid=$(podman create {{ image }}:{{ git_tag }}-{{ arch }})
  trap "podman rm $_cid > /dev/null" EXIT
  mkdir -p {{ tmp }}/rootfs
  podman export $_cid | tar xf - -C {{ tmp }}/rootfs
  [ -n "$BETTERLEAKS_FILE" ] && BETTERLEAKS_OPTS="--no-banner --no-color --report-path $BETTERLEAKS_FILE --report-format sarif"
  betterleaks dir {{ tmp }}/rootfs --validation false --exit-code 0 $BETTERLEAKS_OPTS
  grype dir:{{ tmp }}/rootfs
  rm -rf {{ tmp }}/rootfs

# Log into AWS ECR (public or private). Region inferred from the registry URL for private.
ecr-login:
  #!/usr/bin/env bash
  set -eo pipefail
  case "{{ registry }}" in
    public.ecr.aws*)
      aws ecr-public get-login-password --region us-east-1 \
        | podman login --username AWS --password-stdin "{{ registry }}"
      ;;
    *.dkr.ecr.*.amazonaws.com*)
      region=$(echo "{{ registry }}" | sed -n 's|^[^.]*\.dkr\.ecr\.\([^.]*\)\.amazonaws\.com.*|\1|p')
      aws ecr get-login-password --region "$region" \
        | podman login --username AWS --password-stdin "{{ registry }}"
      ;;
    *)
      echo "Unrecognized ECR registry: {{ registry }}" >&2
      exit 1
      ;;
  esac

# Create a new AWS ECR (public or private) repository
create-repo image=git_repo_name:
  #!/usr/bin/env bash
  set -eo pipefail
  case "{{ registry }}" in
    public.ecr.aws*)
      aws ecr-public create-repository --repository-name {{ image }} --region us-east-1
      ;;
    *.dkr.ecr.*.amazonaws.com*)
      region=$(echo "{{ registry }}" | sed -n 's|^[^.]*\.dkr\.ecr\.\([^.]*\)\.amazonaws\.com.*|\1|p')
      aws ecr create-repository --repository-name {{ image }} --region "$region"
      ;;
    *)
      echo "Unrecognized ECR registry: {{ registry }}" >&2
      exit 1
      ;;
  esac

# tag and push all actual images and multi-arch manifest
push image=git_repo_name extra_tags="": ecr-login
  #!/usr/bin/env bash
  for t in {{ git_tag }} latest {{ extra_tags }}; do
    echo "Tagging image with {{ registry }}/{{ image }}:${t}-{{ arch }}"
    podman tag {{ image }}:{{ git_tag }}-{{ arch }} {{ registry }}/{{ image }}:${t}-{{ arch }}
    podman manifest rm {{ image }}:$t || true
    podman manifest create {{ image }}:$t
    for a in {{ all_archs }}; do
      podman image exists {{ registry }}/{{ image }}:$t-$a && \
      podman manifest add {{ image }}:$t containers-storage:{{ registry }}/{{ image }}:$t-$a
    done
    echo "Pushing manifest {{ image }}:$t"
    podman manifest push --all {{ image }}:$t docker://{{ registry }}/{{ image }}:$t
  done

# cleanup local images
clean image=git_repo_name extra_tags="":
  #!/usr/bin/env bash
  for t in {{ git_tag }} latest {{ extra_tags }}; do
    for a in {{ all_archs }}; do
      podman image exists {{ image }}:$t-$a && podman image rm -f {{ image }}:$t-$a || true
    done
  done

# delete all remote untagged and in-dev images, keep 10 tagged
[no-cd]
rm-remote-untagged image=git_repo_name:
  @echo "Removing all untagged and in-dev images from {{ registry }}/{{ image }}"
  {{ source_directory() }}/ecr_lifecycle.py --repo {{ image }} --registry "{{ registry }}" --dev
