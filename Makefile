REGISTRY := public.ecr.aws/zero-downtime
IMAGE := cloudbender
REGION := us-east-1

include .ci/podman.mk

pytest:
	flake8 cloudbender tests
	TEST=True pytest --log-cli-level=DEBUG

pyclean:
	rm -rf .cache build .coverage .eggs cloudbender.egg-info .pytest_cache dist

pybuild:
	hatchling build

test_upload: pybuild
	twine upload --repository-url https://test.pypi.org/legacy/ --non-interactive dist/cloudbender-*.whl

upload: pybuild
	twine upload -r pypi --non-interactive  dist/cloudbender-*.whl
