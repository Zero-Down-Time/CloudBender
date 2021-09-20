import os
import io
import gzip
import re
import base64
import yaml
import copy
import subprocess
import sys

import jinja2
from jinja2.filters import make_attrgetter
from jinja2.runtime import Undefined

import pyminifier.token_utils
import pyminifier.minification
import pyminifier.compression
import pyminifier.obfuscate
import types

import logging

logger = logging.getLogger(__name__)


@jinja2.contextfunction
def option(context, attribute, default_value=u'', source='options'):
    """ Get attribute from options data structure, default_value otherwise """
    environment = context.environment
    options = environment.globals['_config'][source]

    if not attribute:
        return default_value

    try:
        getter = make_attrgetter(environment, attribute)
        value = getter(options)

        if isinstance(value, Undefined):
            return default_value

        return value

    except (jinja2.exceptions.UndefinedError):
        return default_value


@jinja2.contextfunction
def include_raw_gz(context, files=None, gz=True, remove_comments=False):
    jenv = context.environment
    output = ''

    # For shell script we can even remove whitespaces so treat them individually
    # sed -e '2,$ {/^ *$/d ; /^ *#/d ; /^[ \t] *#/d ; /*^/d ; s/^[ \t]*// ; s/*[ \t]$// ; s/ $//}'
    for name in files:
        output = output + jinja2.Markup(jenv.loader.get_source(jenv, name)[0])

    if remove_comments:
        # Remove full line comments but not shebang
        _re_comment = re.compile(r'^\s*#[^!]')
        _re_blank = re.compile(r'^\s*$')
        _re_keep = re.compile(r'^## template: jinja$')
        stripped_output = ''
        for curline in output.splitlines():
            if re.match(_re_blank, curline):
                continue
            elif re.match(_re_keep, curline):
                stripped_output = stripped_output + curline + '\n'
            elif re.match(_re_comment, curline):
                logger.debug("Removed {}".format(curline))
            else:
                stripped_output = stripped_output + curline + '\n'

        output = stripped_output

    if not gz:
        return(output)

    buf = io.BytesIO()
    f = gzip.GzipFile(mode='w', fileobj=buf, mtime=0)
    f.write(output.encode())
    f.close()

    # MaxSize is 21847
    logger.info("Compressed user-data from {} to {}".format(len(output), len(buf.getvalue())))
    return base64.b64encode(buf.getvalue()).decode('utf-8')


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
def sub(value='', pattern='', replace='', ignorecase=False):
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
    logger.info("Compressed python code from {} to {}".format(len(source), len(minified_source)))
    return minified_source


def inline_yaml(block):
    return yaml.safe_load(block)


def JinjaEnv(template_locations=[]):
    jenv = jinja2.Environment(trim_blocks=True,
                              lstrip_blocks=True,
                              extensions=['jinja2.ext.loopcontrols', 'jinja2.ext.do'])

    if template_locations:
        jinja_loaders = []
        for _dir in template_locations:
            jinja_loaders.append(jinja2.FileSystemLoader(str(_dir)))
        jenv.loader = jinja2.ChoiceLoader(jinja_loaders)

    else:
        jenv.loader = jinja2.BaseLoader()

    jenv.globals['include_raw'] = include_raw_gz
    jenv.globals['raise'] = raise_helper
    jenv.globals['option'] = option

    jenv.filters['sub'] = sub
    jenv.filters['pyminify'] = pyminify
    jenv.filters['inline_yaml'] = inline_yaml

    jenv.tests['match'] = match
    jenv.tests['regex'] = regex
    jenv.tests['search'] = search

    return jenv


def read_config_file(path, variables={}):
    """ reads yaml config file, passes it through jinja and returns data structre

        - OS ENV are available as {{ ENV.<VAR> }}
        - variables defined in parent configs are available as {{ <VAR> }}
    """
    jinja_variables = copy.deepcopy(variables)
    jinja_variables['ENV'] = os.environ

    if path.exists():
        logger.debug("Reading config file: {}".format(path))

        # First check for sops being present
        try:
            jenv = jinja2.Environment(
                enable_async=True,
                auto_reload=False,
                loader=jinja2.FunctionLoader(_sops_loader),
                undefined=jinja2.StrictUndefined,
                extensions=['jinja2.ext.loopcontrols'])
            template = jenv.get_template(str(path))
            rendered_template = template.render(jinja_variables)
            data = yaml.safe_load(rendered_template)
            if data:
                return data

        except Exception as e:
            logger.exception("Error reading config file: {} ({})".format(path, e))
            sys.exit(1)

    return {}


def _sops_loader(path):
    """ Tries to loads yaml file
        If "sops" key is detected the file is piped through sops before returned
    """
    with open(path, 'r') as f:
        config_raw = f.read()
        data = yaml.safe_load(config_raw)

        if data and 'sops' in data:
            try:
                result = subprocess.run([
                    'sops',
                    '--input-type', 'yaml',
                    '--output-type', 'yaml',
                    '--decrypt', '/dev/stdin'
                ], stdout=subprocess.PIPE, input=config_raw.encode('utf-8'),
                    env=dict(os.environ, **{"AWS_SDK_LOAD_CONFIG": "1"}))
            except FileNotFoundError:
                logger.exception("SOPS encrypted config {},  but unable to find sops binary! Try eg: https://github.com/mozilla/sops/releases/download/v3.5.0/sops-v3.5.0.linux".format(path))
                sys.exit(1)

            return result.stdout.decode('utf-8')
        else:
            return config_raw
