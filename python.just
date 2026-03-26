import 'builder.just'
import 'git.just'

fmt:
  uv run autopep8 -ir .

lint:
  uv run flake8 .

# requires 1.45 [arg('target', pattern='(release)?')]
build target="":
  uv build

test:
  uv run pytest --log-cli-level=DEBUG

clean:
  rm -rf .pytest_cache dist

test_upload:
	uv publish --index testpypi --dry-run

upload:
  uv publish
