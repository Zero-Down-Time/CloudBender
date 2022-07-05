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
{% if 'AllowedValues' in parameters[p] or 'AllowedPattern' in parameters[p] %}
{% set format = '`%s%s`' % (parameters[p].get('AllowedValues', ""), parameters[p].get('AllowedPattern', "")) %}
{% endif %}
{% if 'Default' in parameters[p] %}
{% if parameters[p]['Type'].lower() == "string" %}
{% set def = '`"%s"`' % parameters[p]['Default'] %}
{% else %}
{% set def = parameters[p]['Default'] %}
{% endif %}
{% endif %}
| {{ p }} | {{ parameters[p]['Type'] | lower }} | {{ def | d("") }} | {{ format | d("") }} | {{ parameters[p]['Description'] }} | {{ parameters[p]['value'] | d("") }} |
{% endfor %}
{% endif %}

{% if outputs %}
## Outputs
| Output | Description | Value @ {{ timestamp }} |
|--------|-------------|-------------------------|
{% for p in outputs.keys() | sort%}
| {{ p }} | {{ outputs[p]['Description'] }} | {{ outputs[p]['last_value'] | d("") }} |
{% endfor %}
{% endif %}
