# Changelog

## 0.7.5
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
