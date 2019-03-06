import os
import io
import gzip
import re
import base64
import yaml

import jinja2
from jinja2.utils import missing, object_type_repr
from jinja2._compat import string_types

import pyminifier.token_utils
import pyminifier.minification
import pyminifier.compression
import pyminifier.obfuscate
import types


import logging
logger = logging.getLogger(__name__)


@jinja2.contextfunction
def cloudbender_ctx(context, cb_ctx={}, reset=False, command=None, args={}):

    # Reset state
    if reset:
        cb_ctx.clear()
        return

    if 'dependencies' not in cb_ctx:
        cb_ctx['dependencies'] = set()

    if 'mandatory_parameters' not in cb_ctx:
        cb_ctx['mandatory_parameters'] = set()

    if command == 'get_dependencies':
        _deps = sorted(list(cb_ctx['dependencies']))
        if _deps:
            logger.debug("Stack depencies: {}".format(','.join(_deps)))
        return _deps

    elif command == 'add_dependency':
        try:
            cb_ctx['dependencies'].add(args['dep'])
            logger.debug("Adding stack depency to {}".format(args['dep']))
        except KeyError:
            pass

    else:
        raise("Unknown command")


@jinja2.contextfunction
def get_custom_att(context, att=None, ResourceName="FortyTwo", attributes={}, reset=False, dump=False):
    """ Returns the rendered required fragement and also collects all foreign
        attributes for the specified CustomResource to include them later in
        the actual CustomResource include property """

    # Reset state
    if reset:
        attributes.clear()
        return

    # return all registered attributes
    if dump:
        return attributes

    # If called with an attribute, return fragement and register dependency
    if att:
        config = context.get_all()['_config']

        if ResourceName not in attributes:
            attributes[ResourceName] = set()

        attributes[ResourceName].add(att)
        if ResourceName == 'FortyTwo':
            cloudbender_ctx(context, command='add_dependency', args={'dep': att.split('.')[0]})

        if config['cfn']['Mode'] == "FortyTwo":
            return('{{ "Fn::GetAtt": ["{0}", "{1}"] }}'.format(ResourceName, att))
        elif config['cfn']['Mode'] == "AWSImport" and ResourceName == "FortyTwo":
            # AWS only allows - and :, so replace '.' with ":"
            return('{{ "Fn::ImportValue": {{ "Fn::Sub": "${{Conglomerate}}:{0}" }} }}'.format(att.replace('.', ':')))
        else:
            # We need to replace . with some PureAlphaNumeric thx AWS ...
            return('{{ Ref: {0} }}'.format(att.replace('.', 'DoT')))


@jinja2.contextfunction
def include_raw_gz(context, files=None, gz=True):
    jenv = context.environment
    output = ''
    for name in files:
        output = output + jinja2.Markup(jenv.loader.get_source(jenv, name)[0])

    # logger.debug(output)

    if not gz:
        return(output)

    buf = io.BytesIO()
    f = gzip.GzipFile(mode='w', fileobj=buf, mtime=0)
    f.write(output.encode())
    f.close()

    return base64.b64encode(buf.getvalue()).decode('utf-8')


@jinja2.contextfunction
def render_once(context, name=None, resources=set(), reset=False):
    """ Utility function returning True only once per name """

    if reset:
        resources.clear()
        return

    if name and name not in resources:
        resources.add(name)
        return True

    return False


@jinja2.contextfunction
def raise_helper(context, msg):
    raise Exception(msg)


# Custom tests
def regex(value='', pattern='', ignorecase=False, match_type='search'):
    ''' Expose `re` as a boolean filter using the `search` method by default.
        This is likely only useful for `search` and `match` which already
        have their own filters.
    '''
    if ignorecase:
        flags = re.I
    else:
        flags = 0
    _re = re.compile(pattern, flags=flags)
    if getattr(_re, match_type, 'search')(value) is not None:
        return True
    return False


def match(value, pattern='', ignorecase=False):
    ''' Perform a `re.match` returning a boolean '''
    return regex(value, pattern, ignorecase, 'match')


def search(value, pattern='', ignorecase=False):
    ''' Perform a `re.search` returning a boolean '''
    return regex(value, pattern, ignorecase, 'search')


# Custom filters
def regex_replace(value='', pattern='', replace='', ignorecase=False):
    if ignorecase:
        flags = re.I
    else:
        flags = 0
    return re.sub(pattern, replace, value, flags=flags)


def pyminify(source, obfuscate=False, minify=True):
    # pyminifier options
    options = types.SimpleNamespace(
        tabs=False, replacement_length=1, use_nonlatin=0,
        obfuscate=0, obf_variables=1, obf_classes=0, obf_functions=0,
        obf_import_methods=0, obf_builtins=0)

    tokens = pyminifier.token_utils.listified_tokenizer(source)

    if minify:
        source = pyminifier.minification.minify(tokens, options)
        tokens = pyminifier.token_utils.listified_tokenizer(source)

    if obfuscate:
        name_generator = pyminifier.obfuscate.obfuscation_machine(use_unicode=False)
        pyminifier.obfuscate.obfuscate("__main__", tokens, options, name_generator=name_generator)
        # source = pyminifier.obfuscate.apply_obfuscation(source)

    source = pyminifier.token_utils.untokenize(tokens)
    # logger.info(source)
    minified_source = pyminifier.compression.gz_pack(source)
    logger.info("Compressed python code to {}".format(len(minified_source)))
    return minified_source


def parse_yaml(block):
    return yaml.safe_load(block)


class SilentUndefined(jinja2.Undefined):
    '''
    Log warning for undefiend but continue
    '''
    def _fail_with_undefined_error(self, *args, **kwargs):
        if self._undefined_hint is None:
            if self._undefined_obj is missing:
                hint = '%r is undefined' % self._undefined_name
            elif not isinstance(self._undefined_name, string_types):
                hint = '%s has no element %r' % (
                    object_type_repr(self._undefined_obj),
                    self._undefined_name
                )
            else:
                hint = '%r has no attribute %r' % (
                    object_type_repr(self._undefined_obj),
                    self._undefined_name
                )
        else:
            hint = self._undefined_hint

        logger.warning("Undefined variable: {}".format(hint))
        return ''


def JinjaEnv(template_locations=[]):
    jenv = jinja2.Environment(trim_blocks=True,
                              lstrip_blocks=True,
                              undefined=SilentUndefined,
                              extensions=['jinja2.ext.loopcontrols', 'jinja2.ext.do'])

    jinja_loaders = []
    for _dir in template_locations:
        jinja_loaders.append(jinja2.FileSystemLoader(_dir))
    jenv.loader = jinja2.ChoiceLoader(jinja_loaders)

    jenv.globals['include_raw'] = include_raw_gz
    jenv.globals['get_custom_att'] = get_custom_att
    jenv.globals['cloudbender_ctx'] = cloudbender_ctx
    jenv.globals['render_once'] = render_once
    jenv.globals['raise'] = raise_helper

    jenv.filters['regex_replace'] = regex_replace
    jenv.filters['pyminify'] = pyminify
    jenv.filters['yaml'] = parse_yaml

    jenv.tests['match'] = match
    jenv.tests['regex'] = regex
    jenv.tests['search'] = search

    return jenv


def read_config_file(path, jinja_args=None):
    """ reads yaml config file, passes it through jinja and returns data structre """

    if os.path.exists(path):
        logger.debug("Reading config file: {}".format(path))
        try:
            jenv = jinja2.Environment(
                loader=jinja2.FileSystemLoader(os.path.dirname(path)),
                undefined=jinja2.StrictUndefined,
                extensions=['jinja2.ext.loopcontrols'])
            template = jenv.get_template(os.path.basename(path))
            rendered_template = template.render(
                env=os.environ
            )
            data = yaml.safe_load(rendered_template)
            if data:
                return data

        except Exception as e:
            logger.exception("Error reading config file: {} ({})".format(path, e))

    return {}
