import 'builder.just'
import 'git.just'

prepare:
  cargo fetch

lint: prepare
  cargo clippy --workspace -r
  cargo deny check -s

fmt:
  cargo fmt

# requires 1.45 [arg('target', pattern='(release)?')]
build target="": prepare
  #!/usr/bin/env sh
  if [ "{{ target }}" == "release" ]; then
    cargo auditable build --locked --release
  else
    cargo auditable build --locked
  fi

test: build
  cargo test

clean:
  cargo clean

# requires 1.45 [arg('version', pattern='^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$')]
cut-release version: && (_addCommitTagPush version "Cargo.*")
  @sed -i -e "s/^version = \".*/version = \"{{ trim_start_match(version, "v") }}\"/" Cargo.toml
  @cargo update -w
