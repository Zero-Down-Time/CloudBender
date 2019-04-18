# Changelog

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
