.PHONY: test dist test_upload upload

test:
	tox

clean:
	rm -rf .tox .cache dist

dist:
	python setup.py bdist_wheel --universal

test_upload: clean dist
	twine upload --repository-url https://test.pypi.org/legacy/ dist/cloudbender-*.whl

upload: clean dist
	twine upload dist/cloudbender-*.whl
