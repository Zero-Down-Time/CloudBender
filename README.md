# ![Logo](cloudbender.png) CloudBender

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
  create-docs        Parses all documentation fragments out of rendered...
  delete             Deletes stacks or stack groups
  outputs            Prints all stack outputs
  provision          Creates or updates stacks or stack groups
  render             Renders template and its parameters
  sync               Renders template and provisions it right away
  validate           Validates already rendered templates using cfn-lint
```

## Config management
- Within the config folder each directory represents either a stack group if it has sub-directories, or an actual Cloudformation stack in case it is a leaf folder.
- The actual configuration for each stack is hierachly merged. Lower level config files overwrite higher-level values. Complex data structures like dictionaries and arrays are deep merged.

# Secrets

CloudBender supports Mozilla's [SOPS](https://github.com/mozilla/sops) to encrypt values in any config yaml file since version 0.8.1  


If a sops encrypted config file is detected CloudBender will automatically try to decrypt the file during execution.  
All required information to decrypt has to be present in the embedded sops config or set ahead of time via sops supported ENVIRONMENT variables.
