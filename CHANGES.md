# Changelog

## 0.4.1
- add *sync* command combining *render* and *provision* into one task
- make cb (boolean) available in Jinja context to allow easy toggle for features relying on cloudbender in templates

## 0.4.0
- support for environment variables in any config file  
  Example: `profile: {{ env.AWS_DEFAULT_PROFILE }}`
- support for jinja `{% do %}` extension
- support for inline yaml style complex data definitions, via custom jinja filter `yaml`
- missing variables now cause warnings, but rendering continues with ''
