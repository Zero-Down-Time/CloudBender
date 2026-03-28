import 'builder.just'
import 'git.just'

prepare:
  uv sync --locked --no-install-project --no-editable

fmt:
  uv run --extra dev autopep8 -ir .

lint:
  uv run --extra dev flake8 .

# requires 1.45 [arg('target', pattern='(release)?')]
build target="":
  uv build --clear

test:
  uv run --extra dev pytest --log-cli-level=DEBUG

clean:
  rm -rf .venv .pytest_cache dist

test_upload:
	uv publish --index testpypi --dry-run

upload:
  uv publish
