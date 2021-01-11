# Changelog

## 0.9.7
- CloudBender now requires Python >= 3.7
- drop oyaml requirement
- support for short intrinsic functions like !Ref, !Sub etc. by ignoring custom constructors before sending them to AWS to resolve

## 0.9.6
- only upload templates if render is successful
- support for jinja user-data
- tweak for kubezero output template

## 0.9.5
### New Features
Support for uploading and retrieving rendered templates from S3!    

Enabled by setting `template_bucket_url` to a valid S3 location: ```s3://<bucket_name>[/<prefix>]```    
Templates will still be stored and updated in the local file system to allow tracking via git.

## 0.9.4
- new option to generate Dot Graph files via `--graph` option for the create-docs command
- fix validate command using latest cfn-lint library

## 0.9.3
- Improved bash minify for user-data
- Unused additional parameters are now printed as a warning to catch potential typos early

## 0.9.2
- Bug fix release only

## 0.9.1
- Added explicitly set parameter values to the create-doc markdown to get complete stack picture

## 0.9.0
New Features:  

- *Hooks* can now be defined as artifact metadata and are executed at the specified step.  
  Current supported hook entrypoints are: `pre_create, pre_update, post_create, post_update`

    Current implemented hooks:  

    - *cmd*: Allows arbritary commands via subprocess
    - *export_outputs_kubezero*: writes the outputs of kubernetes stacks into a format to be included by KubeZero

- Stack outputs are now written into a yaml file under `outputs` if enabled. Enabled via `options.StoreOutputs`  
  *create-docs* now includes latest stack output values if an output file is found
- Removed deprecated support for storing parameters as these can be constructed any time from existing and tracked configs  

- some code cleanups and minor changes for cli outputs

## 0.8.4
- New Feature: `create-docs` command
  Renders a markdown documentation next to the rendered stack templated by parsing parameters and other relvant metadata

## 0.8.2
- Bug fix release to allow empty stack configs again

## 0.8.1
- Work around for bug in Go AWS SDK to pick up cli credentials, see https://github.com/aws/aws-sdk-go/issues/934

## 0.8.0
- Added support for sops encrypted config files, see: https://github.com/mozilla/sops
- hide stack parameter output in terminal if `NoEcho` is set
- *CloudBender no longer writes stack parameter files to prevent leaking secret values !*  
  These files were never actually used anyways and there sole purpose was to track changes via git.

## 0.7.8
- Add new function `outputs`, to query already deployed stack for their outputs

## 0.7.7
- Add support for CLOUDBENDER_PROJECT_ROOT env variable to specify your root project
- Switch most os.path operations to pathlib to fix various corner cases caused by string matching

## 0.7.6
- Added warning if rendered templates exceed max. inline size of 51200 bytes
- Added optional removal of comments during include_raw processing to reduce user-data size

## 0.7.4
- Fix for only Iterate in use

## 0.7.3
- Added support for variables within config files, incl. usual inheritance
- Set Legacy to False by default, requires templates to check for False explicitly, allows to enabled/disable per stack

## 0.7.2
- Add line numbers to easy debugging
- Fix tests

## 0.7.1
- Release emergency bugfix, 0.7.0 broke recursive option parsing

## 0.7.0
- Add support for SNS Notifcations to Cloudformation create and update operations
- Refactored recursive handling of options withing stack groups

## 0.6.2
- Fixed custom root directory to allow automated template tests

## 0.6.1
- Add support for onfailure for create stack, defaults to DELETE

## 0.6.0
- Implemented Piped mode again
  Allows all stack references to be supplied via injected parameters
  Tries to automatically resolve injected paramteres by inspecting matching outputs from othe running stacks at provision time
- minor bugfixing

## 0.5.2
- Remove tox dependency during build
- Introduce drone.io support
- Makefile cleanup

## 0.5.1
- Automatic dependency resolution to artifacts referred to by StackRef or FortyTwo

## 0.5.0
- new custom Jinja function `sub`, works the same as re.sub
- added possibility to use custom Jinja function `inline_yaml` to set data as yaml
- disabled SilentUndefined
- added Jinja2 extension `do` and `loopcontrols`
- new custom Jinja function `option` to access options at render time incl. default support for nested objects
- removed custom Jinja functions around old remote Ref handling

## 0.4.2
- silence warnings by latest PyYaml 5.1

## 0.4.1
- add *sync* command combining *render* and *provision* into one task
- make cb (boolean) available in Jinja context to allow easy toggle for features relying on cloudbender in templates

## 0.4.0
- support for environment variables in any config file  
  Example: `profile: {{ env.AWS_DEFAULT_PROFILE }}`
- support for jinja `{% do %}` extension
- support for inline yaml style complex data definitions, via custom jinja filter `yaml`
- missing variables now cause warnings, but rendering continues with ''
