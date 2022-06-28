# ![Logo](https://git.zero-downtime.net/ZeroDownTime/CloudBender/media/branch/master/cloudbender.png) CloudBender

# About

Toolset to deploy and maintain infrastructure in automated and trackable manner.  
First class support for:  
- [Pulumi](https://www.pulumi.com/docs/)
- [AWS CloudFormation](https://aws.amazon.com/cloudformation)


# Install

## Container
This most likely only works on a recent Linux box/VM, which is capable of running rootless containers within containers.
Requires kernel >= 5.10, cgroupsV2 support, podman, ...

```
podman run -it --rm -v .:/workspace -v $HOME/.aws/config:/workspace/.aws/config public.ecr.aws/zero-downtime/cloudbender:latest cloudbender version
```

## Local install
1. ```pip install cloudbender```
2. ```curl -fsSL https://get.pulumi.com | sh```, see official [Docs](https://www.pulumi.com/docs/get-started/install/)
3. Ensure you either have `docker` or `podman` in your PATH.

To verify that all pieces are in place run:  
```
cloudbender version
```
which should get you something like:
```
[2022-06-28 16:06:24] CloudBender: 0.13.4
[2022-06-28 16:06:24] Pulumi: v3.34.1
[2022-06-28 16:06:24] Podman/Docker: podman version 4.1.0
```

## State management
### Pulumi
The state for all Pulumi resources are stored on S3 in your account and in the same region as the resources being deployed.
No data is send to nor shared with the official Pulumi provided APIs.

CloudBender configures Pulumi with a local, temporary workspace on the fly. This incl. the injection of various common parameters like the AWS account ID and region etc.  

### Cloudformation
All state is handled by AWS Cloudformation.  
The required account and region are determined by CloudBender automatically from the configuration.


## CLI

```
Usage: cloudbender [OPTIONS] COMMAND [ARGS]...

Options:
  --debug     Turn on debug logging.
  --dir TEXT  Specify cloudbender project directory.
  --help      Show this message and exit.

Commands:
  assimilate         Imports potentially existing resources into Pulumi...
  clean              Deletes all previously rendered files locally
  create-change-set  Creates a change set for an existing stack - CFN only
  create-docs        Parses all documentation fragments out of rendered...
  delete             Deletes stacks or stack groups
  execute            Executes custom Python function within an existing...
  export             Exports a Pulumi stack to repair state
  get-config         Get a config value, decrypted if secret
  outputs            Prints all stack outputs
  preview            Preview of Pulumi stack up operation
  provision          Creates or updates stacks or stack groups
  refresh            Refreshes Pulumi stack / Drift detection
  render             Renders template and its parameters - CFN only
  set-config         Sets a config value, encrypts with stack key if secret
  sync               Renders template and provisions it right away
  validate           Validates already rendered templates using cfn-lint...
  version            Displays own version and all dependencies
```

## Config management
- Within the config folder each directory represents either a stack group if it has sub-directories, or an actual Cloudformation stack in case it is a leaf folder.
- The actual configuration for each stack is hierachly merged. Lower level config files overwrite higher-level values. Complex data structures like dictionaries and arrays are deep merged.

## Secrets handling

### Pulumi
CloudBender supports the native Pulumi secret handling.
See [Pulumi Docs](https://www.pulumi.com/docs/intro/concepts/secrets/) for details.

### Cloudformation
CloudBender supports [SOPS](https://github.com/mozilla/sops) to encrypt values in any config yaml file since version 0.8.1  

If a sops encrypted config file is detected CloudBender will automatically try to decrypt the file during execution.  
All required information to decrypt has to be present in the embedded sops config or set ahead of time via sops supported ENVIRONMENT variables.

SOPS support can be disabled by setting `DISABLE_SOPS` in order to reduce timeouts etc.
