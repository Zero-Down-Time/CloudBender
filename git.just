### Git 

# Parse version from latest git semver tag, use short commit otherwise
git_tag := `git describe --tags --match v*.*.* --dirty 2>/dev/null || git describe --match="" --always --dirty 2>/dev/null || echo "untagged"`
git_branch := `git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "HEAD"`
git_repo_name := `basename -s .git $(git config --get remote.origin.url 2>/dev/null || echo "unknown")`

# Compute tag with branch suffix if needed
tag := if git_branch =~ "^(main|master)$" {
  git_tag
} else if git_tag =~ git_branch {
  git_tag
} else if git_tag == git_branch {
  git_tag
} else {
  git_tag + "-" + replace_regex(git_branch, "[^a-zA-Z0-9]+", "-")
}

[confirm("Commit, tag and push ?")]
_addCommitTagPush version +files:
  git add {{ files }}
  git commit -m "ci: Release {{ version }}"
  git tag -a {{ version }} -m"Release: {{ version }}"
  git push && git push --tags


### OS
arch := "amd64"
all_archs := "amd64 arm64"

# Validate architecture (defaults to amd64)
_validate-arch arch="amd64":
  #!/usr/bin/env bash
  if [[ "{{arch}}" != "amd64" && "{{arch}}" != "arm64" ]]; then
    echo "Error: Invalid arch '{{arch}}'. Must be 'amd64' or 'arm64'"
    exit 1
  fi

_git-debug: (_validate-arch arch)
  @echo "tag: {{git_tag}} branch: {{ git_branch }} on {{arch}}".

# pull latest shared .ci subtree
ci-pull-upstream:
	git subtree pull --prefix .ci ssh://git@git.zero-downtime.net/ZeroDownTime/ci-tools-lib.git main --squash -m "Merge latest ci-tools-lib"

cleanup-tags tag:
  #!/usr/bin/env bash
  if ! git rev-parse "refs/tags/{{tag}}" >/dev/null 2>&1; then
    echo "Error: tag {{tag}} does not exist" >&2
    exit 1
  fi

  TAGS=$(git tag --sort=version:refname | sed "/{{tag}}/q" | head -n -1)
  COUNT=$(echo "$TAGS" | grep -c . || true)
  echo "Deleting $COUNT tags up to {{tag}}"

  if [ "$COUNT" -gt 0 ]; then
    echo "$TAGS" | xargs -I{} git tag -d {}
    echo "$TAGS" | sed 's|.*|:refs/tags/&|' | xargs git push origin
  fi
