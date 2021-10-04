import sys
import os
import re
import shutil
import importlib
import pkg_resources
import pulumi

import logging
logger = logging.getLogger(__name__)


def pulumi_init(stack):

    # Fail early if pulumi binaries are not available
    if not shutil.which('pulumi'):
        raise FileNotFoundError("Cannot find pulumi binary, see https://www.pulumi.com/docs/get-started/install/")

    # add all artifact_paths/pulumi to the search path for easier imports in the pulumi code
    for artifacts_path in stack.ctx['artifact_paths']:
        _path = '{}/pulumi'.format(artifacts_path.resolve())
        sys.path.append(_path)

    # Try local implementation first, similar to Jinja2 mode
    _found = False
    try:
        _stack = importlib.import_module('config.{}.{}'.format(stack.rel_path, stack.template).replace('/', '.'))
        _found = True

    except ImportError:
        for artifacts_path in stack.ctx['artifact_paths']:
            try:
                spec = importlib.util.spec_from_file_location("_stack", '{}/pulumi/{}.py'.format(artifacts_path.resolve(), stack.template))
                _stack = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(_stack)
                _found = True

            except FileNotFoundError:
                pass

    if not _found:
        raise FileNotFoundError("Cannot find Pulumi implementation for {}".format(stack.stackname))

    project_name = stack.parameters['Conglomerate']

    # Remove stacknameprefix if equals Conglomerate as Pulumi implicitly prefixes project_name
    pulumi_stackname = re.sub(r'^' + project_name + '-?', '', stack.stackname)
    try:
        pulumi_backend = '{}/{}/{}'.format(stack.pulumi['backend'], project_name, stack.region)

    except KeyError:
        raise KeyError('Missing pulumi.backend setting !')

    account_id = stack.connection_manager.call('sts', 'get_caller_identity', profile=stack.profile, region=stack.region)['Account']
    # Ugly hack as Pulumi currently doesnt support MFA_TOKENs during role assumptions
    # Do NOT set them via 'aws:secretKey' as they end up in the stack.json in plain text !!!
    if stack.connection_manager._sessions[(stack.profile, stack.region)].get_credentials().token:
        os.environ['AWS_SESSION_TOKEN'] = stack.connection_manager._sessions[(stack.profile, stack.region)].get_credentials().token

    os.environ['AWS_ACCESS_KEY_ID'] = stack.connection_manager._sessions[(stack.profile, stack.region)].get_credentials().access_key
    os.environ['AWS_SECRET_ACCESS_KEY'] = stack.connection_manager._sessions[(stack.profile, stack.region)].get_credentials().secret_key
    os.environ['AWS_DEFAULT_REGION'] = stack.region

    # Secrets provider
    try:
        secrets_provider = stack.pulumi['secretsProvider']
        if secrets_provider == 'passphrase' and 'PULUMI_CONFIG_PASSPHRASE' not in os.environ:
            raise ValueError('Missing PULUMI_CONFIG_PASSPHRASE environment variable!')

    except KeyError:
        logger.warning('Missing pulumi.secretsProvider setting, secrets disabled !')
        secrets_provider = None

    # Set tag for stack file name and version
    _tags = stack.tags
    try:
        _version = _stack.VERSION
    except AttributeError:
        _version = 'undefined'

    _tags['zero-downtime.net/cloudbender'] = '{}:{}'.format(os.path.basename(_stack.__file__), _version)

    _config = {
        "aws:region": stack.region,
        "aws:profile": stack.profile,
        "aws:defaultTags": {"tags": _tags},
        "zdt:region": stack.region,
        "zdt:awsAccountId": account_id,
    }

    # inject all parameters as config in the <Conglomerate> namespace
    for p in stack.parameters:
        _config['{}:{}'.format(stack.parameters['Conglomerate'], p)] = stack.parameters[p]

    stack_settings = pulumi.automation.StackSettings(
        config=_config,
        secrets_provider=secrets_provider,
        encryption_salt=stack.pulumi.get('encryptionsalt', None),
        encrypted_key=stack.pulumi.get('encryptedkey', None)
    )

    project_settings = pulumi.automation.ProjectSettings(
        name=project_name,
        runtime="python",
        backend={"url": pulumi_backend})

    ws_opts = pulumi.automation.LocalWorkspaceOptions(
        work_dir=stack.work_dir,
        project_settings=project_settings,
        stack_settings={pulumi_stackname: stack_settings},
        secrets_provider=secrets_provider)

    stack = pulumi.automation.create_or_select_stack(stack_name=pulumi_stackname, project_name=project_name, program=_stack.pulumi_program, opts=ws_opts)
    stack.workspace.install_plugin("aws", pkg_resources.get_distribution("pulumi_aws").version)

    return stack
