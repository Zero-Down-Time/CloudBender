#!/usr/bin/env python
import io
import os
import re
from setuptools import setup
from setuptools import find_packages
from setuptools.command.test import test as TestCommand

class PyTest(TestCommand):
    """TestCommand subclass to use pytest with setup.py test."""

    def finalize_options(self):
        """Find our package name and test options to fill out test_args."""

        TestCommand.finalize_options(self)
        self.test_args = ['-rx', '--cov', 'cloudbender',
            '--cov-report', 'term-missing']
        self.test_suite = True

    def run_tests(self):
        """Taken from http://pytest.org/latest/goodpractises.html."""

        # have to import here, outside the eggs aren't loaded
        import pytest
        errno = pytest.main(self.test_args)
        raise SystemExit(errno)


if os.path.isfile("README.md"):
    with io.open("README.md", encoding="utf-8") as opendescr:
        long_description = opendescr.read()
else:
    long_description = __doc__

setup(
    name='cloudbender',
    setuptools_git_versioning={
        "enabled": True,
    },
    setup_requires=["setuptools-git-versioning"],
    description='Toolset to render and manage AWS Cloudformation',
    python_requires='>=3.7',
    long_description=long_description,
    long_description_content_type="text/markdown",
    author='Stefan Reimer',
    author_email='stefan@zero-downtime.net',
    url='https://git.zero-downtime.net/ZeroDownTime/CloudBender',
    packages=find_packages(),
    package_data={ 'cloudbender': ['templates/*.md', 'templates/*.yaml'], },
    include_package_data=True,
    entry_points={'console_scripts': [ "cloudbender = cloudbender.cli:cli" ]},
    install_requires=['boto3', 'Jinja2', 'click', 'cfn-lint>=0.34', 'pyminifier', 'pulumi>=3.0.0,<4.0.0', 'pulumi-aws>=4.0.0,<5.0.0'], #'apprise'
    tests_require=["pytest-cov", "moto", "mock", 'pytest'],
    cmdclass={"test": PyTest},
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "Operating System :: POSIX",
        "Programming Language :: Python",
        "License :: OSI Approved :: GNU Affero General Public License v3"
    ])
