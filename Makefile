REGISTRY := public.ecr.aws/zero-downtime
IMAGE := cloudbender
REGION := us-east-1

# Parse version from latest git semver tag
GTAG=$(shell git describe --tags --match v*.*.* 2>/dev/null || git rev-parse --short HEAD 2>/dev/null)
TAG ?= $(shell echo $(GTAG) | awk -F '-' '{ print $$1 "-" $$2 }' | sed -e 's/-$$//')

ifneq ($(TRIVY_REMOTE),)
  TRIVY_OPTS := --server ${TRIVY_REMOTE}
endif

.PHONY: pytest pybuild test_upload upload all build test push scan

all: pytest pybuild

pytest:
	flake8 cloudbender tests
	TEST=True pytest --log-cli-level=DEBUG

clean:
	rm -rf .cache build .coverage .eggs cloudbender.egg-info .pytest_cache dist

pybuild:
	hatchling build

test_upload: pybuild
	twine upload --repository-url https://test.pypi.org/legacy/ --non-interactive dist/cloudbender-*.whl

upload: pybuild
	twine upload -r pypi --non-interactive  dist/cloudbender-*.whl

build:
	podman build --rm -t $(IMAGE):$(TAG) -t $(IMAGE):latest .

test:
	@echo "Not implemented (yet)"

push:
	aws ecr-public get-login-password --region $(REGION) | podman login --username AWS --password-stdin $(REGISTRY)
	podman tag $(IMAGE):latest $(REGISTRY)/$(IMAGE):$(TAG) $(REGISTRY)/$(IMAGE):latest
	podman push $(REGISTRY)/$(IMAGE):$(TAG)
	podman push $(REGISTRY)/$(IMAGE):latest

scan:
	trivy image $(TRIVY_OPTS) $(IMAGE):$(TAG)

# Delete all untagged images
.PHONY: rm-remote-untagged
rm-remote-untagged:
	@echo "Removing all untagged images from $(IMAGE) in $(REGION)"
	@aws ecr-public batch-delete-image --repository-name $(IMAGE) --region $(REGION) --image-ids $$(for image in $$(aws ecr-public describe-images --repository-name $(IMAGE) --region $(REGION) --output json | jq -r '.imageDetails[] | select(.imageTags | not ).imageDigest'); do echo -n "imageDigest=$$image "; done)
