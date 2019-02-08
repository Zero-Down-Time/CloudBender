# CloudBender

# About

Toolset to render and manage [AWS CloudFormation](https://aws.amazon.com/cloudformation).


# Install

`$ pip install cloudbender`


# CLI

```
Usage: cloudbender [OPTIONS] COMMAND [ARGS]...

Options:
  --version   Show the version and exit.
  --debug     Turn on debug logging.
  --dir TEXT  Specify cloudbender project directory.
  --help      Show this message and exit.

Commands:
  clean              Deletes all previously rendered files locally
  create-change-set  Creates a change set for an existing stack
  delete             Deletes stacks or stack groups
  provision          Creates or updates stacks or stack groups
  render             Renders template and its parameters
  validate           Validates already rendered templates using cfn-lint
```
