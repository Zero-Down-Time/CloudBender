# CloudBender ![Logo](cloudbender.png)

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
  sync               Renders template and provisions it right away
  validate           Validates already rendered templates using cfn-lint
```

# Secrets

CloudBender supports Mozilla's [SOPS](https://github.com/mozilla/sops) to encrypt values in any config yaml file since version 0.8.1  


If a sops encrypted config file is detected CloudBender will automatically try to decrypt the file during execution.  
All required information to decrypt has to be present in the embedded sops config or set ahead of time via sops supported ENVIRONMENT variables.
