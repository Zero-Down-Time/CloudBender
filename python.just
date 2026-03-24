import 'builder.just'
import 'git.just'

fmt:
  autopep8 -ir .

lint:
  flake8

# requires 1.45 [arg('target', pattern='(release)?')]
build target="":
  hatchling build

test: build
  TEST=True pytest --log-cli-level=DEBUG

clean:
  rm -rf .pytest_cache dist

test_upload: build
	twine upload --repository-url https://test.pypi.org/legacy/ --non-interactive dist/*.whl

upload: build
	twine upload -r pypi --non-interactive  dist/*.whl
