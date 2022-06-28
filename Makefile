REGISTRY := public.ecr.aws/zero-downtime
REPOSITORY := cloudbender
REGION := us-east-1

# Parse version from latest git semver tag
GTAG=$(shell git describe --tags --match v*.*.* 2>/dev/null || git rev-parse --short HEAD 2>/dev/null)
TAG ?= $(shell echo $(GTAG) | awk -F '-' '{ print $$1 "-" $$2 }' | sed -e 's/-$$//')

ifeq ($(TRIVY_REMOTE),)
  TRIVY_OPTS := image
else
  TRIVY_OPTS := client --remote ${TRIVY_REMOTE}
endif

.PHONY: pytest build test_upload upload all dev_setup pybuild

all: pytest pybuild

pytest:
	flake8 cloudbender tests
	TEST=True pytest --log-cli-level=DEBUG

clean:
	rm -rf .cache build .coverage .eggs cloudbender.egg-info .pytest_cache dist

pybuild:
	hatchling build

test_upload: $(PACKAGE_FILE)
	twine upload --repository-url https://test.pypi.org/legacy/ dist/cloudbender-*.whl

upload: $(PACKAGE_FILE)
	twine upload --repository-url https://upload.pypi.org/legacy/ dist/cloudbender-*.whl

build:
	podman build --rm -t $(REPOSITORY):$(TAG) -t $(REPOSITORY):latest .

test:
	@echo "Not implemented (yet)"

push:
	aws ecr-public get-login-password --region $(REGION) | podman login --username AWS --password-stdin $(REGISTRY)
	podman tag $(REPOSITORY):latest $(REGISTRY)/$(REPOSITORY):$(TAG) $(REGISTRY)/$(REPOSITORY):latest
	podman push $(REGISTRY)/$(REPOSITORY):$(TAG)
	podman push $(REGISTRY)/$(REPOSITORY):latest
	# Delete all untagged images
	# aws ecr-public batch-delete-image --repository-name $(REPOSITORY) --region $(REGION) --image-ids $$(for image in $$(aws ecr-public describe-images --repository-name $(REPOSITORY) --region $(REGION) --output json | jq -r '.imageDetails[] | select(.imageTags | not ).imageDigest'); do echo -n "imageDigest=$$image "; done)

scan:
	trivy $(TRIVY_OPTS) $(REPOSITORY):$(TAG)
