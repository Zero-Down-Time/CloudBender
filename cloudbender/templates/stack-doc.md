{{ name }}
===
{{ description }}

{% if dependencies %}
## Dependencies
{% for d in dependencies|sort %}
- {{ d }}
{% endfor %}
{% endif %}

{% if parameters %}
## Parameters
| Parameter | Type | Default | Format | Description | set value @ {{ timestamp }} |
|-----------|------|---------|--------|-------------|-------------------------|
{% for p in parameters.keys() %}
{% if parameters[p]['AllowedValues'] or parameters[p]['AllowedPattern'] %}
{% set format = '`%s%s`' % (parameters[p]['AllowedValues'], parameters[p]['AllowedPattern']) %}
{% endif %}
{% if parameters[p]['Default'] and parameters[p]['Type'].lower() == "string" %}
{% set def = '`"%s"`' % parameters[p]['Default'] %}
{% else %}
{% set def = parameters[p]['Default'] %}
{% endif %}
| {{ p }} | {{ parameters[p]['Type'] | lower }} | {{ def }} | {{ format }} | {{ parameters[p]['Description'] }} | {{ parameters[p]['value'] }} |
{% endfor %}
{% endif %}

{% if outputs %}
## Outputs
| Output | Description | Value @ {{ timestamp }} |
|--------|-------------|-------------------------|
{% for p in outputs.keys() | sort%}
| {{ p }} | {{ outputs[p]['Description'] }} | {{ outputs[p]['last_value'] }} |
{% endfor %}
{% endif %}
