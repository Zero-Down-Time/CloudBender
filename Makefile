VERSION ?= $(shell grep '__version__' cloudbender/__init__.py | cut -d' ' -f3 | cut -d'-' -f1 | sed -e 's/"//g')
PACKAGE_FILE := dist/cloudbender-$(VERSION).py3-none-any.whl

REGISTRY := public.ecr.aws/zero-downtime
REPOSITORY := cloudbender
TAG := $(REPOSITORY):v$(VERSION)

.PHONY: test build test_upload upload all dev_setup docker

all: test build

dev_setup:
	pip install -r requirements.txt --user

test:
	flake8 --ignore=E501 cloudbender tests
	TEST=True pytest --log-cli-level=DEBUG

clean:
	rm -rf .cache build .coverage .eggs cloudbender.egg-info .pytest_cache dist

build: $(PACKAGE_FILE)

$(PACKAGE_FILE):
	python setup.py bdist_wheel --universal

test_upload: $(PACKAGE_FILE)
	twine upload --repository-url https://test.pypi.org/legacy/ dist/cloudbender-*.whl

upload: $(PACKAGE_FILE)
	twine upload --repository-url https://upload.pypi.org/legacy/ dist/cloudbender-*.whl

docker:
	podman build --rm --squash-all --build-arg version=$(VERSION) -t $(TAG) .

push:
	aws ecr-public get-login-password --region us-east-1 | podman login --username AWS --password-stdin $(REGISTRY)
	podman tag $(TAG) $(REGISTRY)/$(TAG)
	podman push $(REGISTRY)/$(TAG)

scan:
	podman system service&
	sleep 3; trivy $(TAG)
