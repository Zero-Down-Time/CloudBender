VERSION ?= $(shell grep '__version__' cloudbender/__init__.py | cut -d' ' -f3 | cut -d'-' -f1 | sed -e 's/"//g')
PACKAGE_FILE := dist/cloudbender-$(VERSION)-py2.py3-none-any.whl

.PHONY: test build test_upload upload all

all: test build

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
	twine upload dist/cloudbender-*.whl
