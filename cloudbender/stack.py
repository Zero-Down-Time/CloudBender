import os
import re
import hashlib
import oyaml as yaml
import time
import pathlib
import pprint

from datetime import datetime, timedelta
from dateutil.tz import tzutc

from botocore.exceptions import ClientError

from .utils import dict_merge, search_refs, ensure_dir
from .connection import BotoConnection
from .jinja import JinjaEnv, read_config_file
from . import __version__
from .exceptions import ParameterNotFound, ParameterIllegalValue
from .hooks import exec_hooks

import cfnlint.core

try:
    import importlib.resources as pkg_resources
except ImportError:
    import importlib_resources as pkg_resources
from . import templates

import logging
logger = logging.getLogger(__name__)


class StackStatus(object):
    """
    StackStatus stores simplified stack statuses.
    """
    COMPLETE = "complete"
    FAILED = "failed"
    IN_PROGRESS = "in progress"
    PENDING = "pending"


class Stack(object):
    def __init__(self, name, template, path, rel_path, ctx):
        self.stackname = name
        self.template = template
        self.path = pathlib.Path(path)
        self.rel_path = rel_path
        self.ctx = ctx

        self.tags = {}
        self.parameters = {}
        self.outputs = {}
        self.options = {'Legacy': False}
        self.region = 'global'
        self.profile = ''
        self.onfailure = 'DELETE'
        self.notfication_sns = []

        self.id = (self.profile, self.region, self.stackname)
        self.aws_stackid = None

        self.md5 = None
        self.mode = 'CloudBender'
        self.provides = template
        self.cfn_template = None
        self.cfn_parameters = []
        self.cfn_data = None
        self.connection_manager = BotoConnection(self.profile, self.region)
        self.status = None
        self.store_outputs = False
        self.dependencies = set()
        self.hooks = {'post_create': [], 'post_update': [], 'pre_create': [], 'pre_update': []}
        self.default_lock = None
        self.multi_delete = True

    def dump_config(self):
        logger.debug("<Stack {}: {}>".format(self.id, pprint.pformat(vars(self))))

    def read_config(self, sg_config={}):
        """ reads stack config """

        # First set various attributes based on parent stackgroup config
        self.tags.update(sg_config.get('tags', {}))
        self.parameters.update(sg_config.get('parameters', {}))
        self.options.update(sg_config.get('options', {}))

        if 'region' in sg_config:
            self.region = sg_config['region']
        if 'profile' in sg_config:
            self.profile = sg_config['profile']
        if 'notfication_sns' in sg_config:
            self.notfication_sns = sg_config['notfication_sns']

        _config = read_config_file(self.path, sg_config.get('variables', {}))
        for p in ["region", "stackname", "template", "default_lock", "multi_delete", "provides", "onfailure", "notification_sns"]:
            if p in _config:
                setattr(self, p, _config[p])

        for p in ["parameters", "tags"]:
            if p in _config:
                setattr(self, p, dict_merge(getattr(self, p), _config[p]))

        # Inject Artifact if not explicitly set
        if 'Artifact' not in self.tags:
            self.tags['Artifact'] = self.provides

        # backwards comp
        if 'vars' in _config:
            logger.warn("vars: in config is deprecated, please use options: instead")
            self.options = dict_merge(self.options, _config['vars'])

        if 'options' in _config:
            self.options = dict_merge(self.options, _config['options'])

        if 'Mode' in self.options:
            self.mode = self.options['Mode']

        if 'StoreOutputs' in self.options:
            self.store_outputs = True

        if 'dependencies' in _config:
            for dep in _config['dependencies']:
                self.dependencies.add(dep)

        # Some sanity checks
        if self.onfailure not in ["DO_NOTHING", "ROLLBACK", "DELETE"]:
            raise ParameterIllegalValue("onfailure must be one of DO_NOTHING | ROLLBACK | DELETE")

        logger.debug("Stack {} added.".format(self.id))

    def render(self):
        """Renders the cfn jinja template for this stack"""

        template_metadata = {
            'Template.Name': self.template,
            'Template.Hash': "__HASH__",
            'CloudBender.Version': __version__
        }
        _config = {'mode': self.mode, 'options': self.options, 'metadata': template_metadata}

        jenv = JinjaEnv(self.ctx['artifact_paths'])
        jenv.globals['_config'] = _config

        template = jenv.get_template('{0}{1}'.format(self.template, '.yaml.jinja'))

        logger.info('Rendering %s', template.filename)

        try:
            self.cfn_template = template.render(_config)
            self.cfn_data = yaml.safe_load(self.cfn_template)
        except Exception as e:
            # In case we rendered invalid yaml this helps to debug
            if self.cfn_template:
                _output = ""
                for i, line in enumerate(self.cfn_template.splitlines(), start=1):
                    _output = _output + '{}: {}\n'.format(i, line)
                logger.error(_output)
            raise e

        if not re.search('CloudBender::', self.cfn_template) and not re.search('Iterate:', self.cfn_template):
            logger.info("CloudBender not required -> removing Transform and Conglomerate parameter")
            self.cfn_template = self.cfn_template.replace('Transform: [CloudBender]', '')

            _res = """
  Conglomerate:
    Type: String
    Description: Project / Namespace this stack is part of
"""
            self.cfn_template = re.sub(_res, '', self.cfn_template)

        # Add Legacy FortyTwo resource to prevent AWS from replacing existing resources for NO reason ;-(
        include = []
        search_refs(self.cfn_data, include, self.mode)
        if self.mode != "Piped" and len(include) and self.options['Legacy']:
            _res = """
  FortyTwo:
    Type: Custom::FortyTwo
    Properties:
      ServiceToken:
        Fn::Sub: "arn:aws:lambda:${{AWS::Region}}:${{AWS::AccountId}}:function:FortyTwo"
      UpdateToken: __HASH__
      Include: {}""".format(sorted(set(include)))

            self.cfn_template = re.sub(r'Resources:', r'Resources:' + _res + '\n', self.cfn_template)
            logger.info("Legacy Mode -> added Custom::FortyTwo")

        elif self.mode == "Piped" and len(include):
            _res = ""
            for attr in include:
                _res = _res + """
  {0}:
    Type: String
    Description: Parameter to provide remote stack attribute {0}""".format(attr)

            self.cfn_template = re.sub(r'Parameters:', r'Parameters:' + _res + '\n', self.cfn_template)
            logger.info("Piped mode: Added parameters for remote stack references")

        # Re-read updated template
        self.cfn_data = yaml.safe_load(self.cfn_template)

        # Check for empty top level Parameters, Outputs and Conditions and remove
        for key in ['Parameters', 'Outputs', 'Conditions']:
            if key in self.cfn_data and not self.cfn_data[key]:
                del self.cfn_data[key]
                self.cfn_template = self.cfn_template.replace('\n' + key + ":", '')

        # Remove and condense multiple empty lines
        self.cfn_template = re.sub(r'\n\s*\n', '\n\n', self.cfn_template)
        self.cfn_template = re.sub(r'^\s*', '', self.cfn_template)
        self.cfn_template = re.sub(r'\s*$', '', self.cfn_template)

        # set md5 last
        self.md5 = hashlib.md5(self.cfn_template.encode('utf-8')).hexdigest()
        self.cfn_template = self.cfn_template.replace('__HASH__', self.md5)

        # Update internal data structures
        self._parse_metadata()

    def _parse_metadata(self):
        # Extract dependencies
        try:
            for dep in self.cfn_data['Metadata']['CloudBender']['Dependencies']:
                self.dependencies.add(dep)
        except KeyError:
            pass

        # Add CloudBender or FortyTwo dependencies
        include = []
        search_refs(self.cfn_data, include, self.mode)
        for ref in include:
            if self.mode != "Piped":
                self.dependencies.add(ref.split('.')[0])
            else:
                self.dependencies.add(ref.split('DoT')[0])

        # Extract hooks
        try:
            for hook, func in self.cfn_data['Metadata']['Hooks'].items():
                if hook in ['post_update', 'post_create', 'pre_create', 'pre_update']:
                    if isinstance(func, list):
                        self.hooks[hook].extend(func)
                    else:
                        self.hooks[hook].append(func)
        except KeyError:
            pass

    def write_template_file(self):
        if self.cfn_template:
            yaml_file = os.path.join(self.ctx['template_path'], self.rel_path, self.stackname + ".yaml")
            ensure_dir(os.path.join(self.ctx['template_path'], self.rel_path))
            with open(yaml_file, 'w') as yaml_contents:
                yaml_contents.write(self.cfn_template)
                logger.info('Wrote %s to %s', self.template, yaml_file)
                if len(self.cfn_template) > 51200:
                    logger.warning("Rendered template exceeds maximum allowed size of 51200, actual size: {} !".format(len(self.cfn_template)))

        else:
            logger.error('No cfn template rendered yet for stack {}.'.format(self.stackname))

    def delete_template_file(self):
        yaml_file = os.path.join(self.ctx['template_path'], self.rel_path, self.stackname + ".yaml")
        try:
            os.remove(yaml_file)
            logger.debug('Deleted cfn template %s.', yaml_file)
        except OSError:
            pass

    def read_template_file(self):
        """ Reads rendered yaml template from disk and extracts metadata """
        if not self.cfn_template:
            yaml_file = os.path.join(self.ctx['template_path'], self.rel_path, self.stackname + ".yaml")

            try:
                with open(yaml_file, 'r') as yaml_contents:
                    self.cfn_template = yaml_contents.read()
                    logger.debug('Read cfn template %s.', yaml_file)

                self.cfn_data = yaml.safe_load(self.cfn_template)
                self._parse_metadata()
            except FileNotFoundError as e:
                logger.warn("Could not find template file: {}".format(yaml_file))
                raise e
        else:
            logger.debug('Using cached cfn template %s.', self.stackname)

    def validate(self):
        """Validates the rendered template via cfn-lint"""
        self.read_template_file()

        try:
            ignore_checks = self.cfn_data['Metadata']['cfnlint_ignore']
        except KeyError:
            ignore_checks = []

        # Ignore some more checks around injected parameters as we generate these
        if self.mode == "Piped":
            ignore_checks = ignore_checks + ['W2505', 'W2509', 'W2507']

        # Ignore checks regarding overloaded properties
        if self.mode == "CloudBender":
            ignore_checks = ignore_checks + ['E3035', 'E3002', 'E3012', 'W2001', 'E3001', 'E0002', 'E1012']

        filename = os.path.join(self.ctx['template_path'], self.rel_path, self.stackname + ".yaml")
        logger.info('Validating {0}'.format(filename))

        lint_args = ['--template', filename]
        if ignore_checks:
            lint_args.append('--ignore-checks')
            lint_args = lint_args + ignore_checks
            logger.info('Ignoring checks: {}'.format(','.join(ignore_checks)))

        (args, filenames, formatter) = cfnlint.core.get_args_filenames(lint_args)
        (template, rules, matches) = cfnlint.core.get_template_rules(filename, args)
        if not matches:
            matches.extend(cfnlint.core.run_cli(filename, template, rules, ['us-east-1'], None))
        if len(matches):
            for match in matches:
                logger.error(formatter._format(match))
        else:
            logger.info("Passed.")

    def get_outputs(self, include='.*', values=False):
        """ gets outputs of the stack """

        try:
            stacks = self.connection_manager.call(
                "cloudformation",
                "describe_stacks",
                {'StackName': self.stackname},
                profile=self.profile, region=self.region)['Stacks']

            try:
                for output in stacks[0]['Outputs']:
                    self.outputs[output['OutputKey']] = output['OutputValue']
                logger.debug("Stack outputs for {} in {}: {}".format(self.stackname, self.region, self.outputs))
            except KeyError:
                pass

        except ClientError:
            logger.warn("Could not get outputs of {}".format(self.stackname))
            pass

        if self.outputs:
            logger.info('{} {} Outputs:\n{}'.format(self.region, self.stackname, pprint.pformat(self.outputs, indent=2)))
            if self.store_outputs:
                self.write_outputs_file()

    def write_outputs_file(self, template='outputs.yaml', filename=False):
        if not filename:
            output_file = os.path.join(self.ctx['outputs_path'], self.rel_path, self.stackname + ".yaml")
        else:
            output_file = os.path.join(self.ctx['outputs_path'], self.rel_path, filename)

        ensure_dir(os.path.join(self.ctx['outputs_path'], self.rel_path))

        my_template = pkg_resources.read_text(templates, template)
        jenv = JinjaEnv()
        template = jenv.from_string(my_template)
        data = {'stackname': "/".join([self.rel_path, self.stackname]), 'timestamp': datetime.strftime(datetime.now(tzutc()), "%d/%m/%y %H:%M"), 'outputs': self.outputs, 'parameters': self.parameters}

        with open(output_file, 'w') as output_contents:
            output_contents.write(template.render(**data))
            logger.info('Wrote outputs for %s to %s', self.stackname, output_file)

    def create_docs(self, template=False):
        """ Read rendered template, parse documentation fragments, eg. parameter description
            and create a mardown doc file for the stack
            same idea as eg. helm-docs for values.yaml
         """

        try:
            self.read_template_file()
        except FileNotFoundError:
            return

        if not template:
            doc_template = pkg_resources.read_text(templates, 'stack-doc.md')
            jenv = JinjaEnv()
            template = jenv.from_string(doc_template)
            data = {}
        else:
            doc_template = template

        data['name'] = self.stackname
        data['description'] = self.cfn_data['Description']
        data['dependencies'] = self.dependencies

        if 'Parameters' in self.cfn_data:
            data['parameters'] = self.cfn_data['Parameters']
            set_parameters = self.resolve_parameters()
            for p in set_parameters:
                data['parameters'][p]['value'] = set_parameters[p]

        if 'Outputs' in self.cfn_data:
            data['outputs'] = self.cfn_data['Outputs']

            # Check for existing outputs yaml, if found add current value column and set header to timestamp from outputs file
            output_file = os.path.join(self.ctx['outputs_path'], self.rel_path, self.stackname + ".yaml")

            try:
                with open(output_file, 'r') as yaml_contents:
                    outputs = yaml.safe_load(yaml_contents.read())
                    for p in outputs['Outputs']:
                        data['outputs'][p]['last_value'] = outputs['Outputs'][p]
                    data['timestamp'] = outputs['TimeStamp']
            except (FileNotFoundError, KeyError):
                pass

        doc_file = os.path.join(self.ctx['docs_path'], self.rel_path, self.stackname + ".md")
        ensure_dir(os.path.join(self.ctx['docs_path'], self.rel_path))

        with open(doc_file, 'w') as doc_contents:
            doc_contents.write(template.render(**data))
            logger.info('Wrote documentation for %s to %s', self.stackname, doc_file)

    def resolve_parameters(self):
        """ Renders parameters for the stack based on the source template and the environment configuration """

        self.read_template_file()

        # if we run in Piped Mode, inspect all outputs of the running Conglomerate members
        if self.mode == "Piped":
            stack_outputs = {}
            try:
                stack_outputs = self._inspect_stacks(self.tags['Conglomerate'])
            except KeyError:
                pass

        _found = {}
        if 'Parameters' in self.cfn_data:
            _errors = []
            self.cfn_parameters = []
            for p in self.cfn_data['Parameters']:
                # In Piped mode we try to resolve all Paramters first via stack_outputs
                if self.mode == "Piped":
                    try:
                        # first reverse the rename due to AWS alphanumeric restriction for parameter names
                        _p = p.replace('DoT', '.')
                        value = str(stack_outputs[_p])
                        self.cfn_parameters.append({'ParameterKey': p, 'ParameterValue': value})
                        logger.info('Got {} = {} from running stack'.format(p, value))
                        continue
                    except KeyError:
                        pass

                # Key name in config tree is: stacks.<self.stackname>.parameters.<parameter>
                if p in self.parameters:
                    value = str(self.parameters[p])
                    self.cfn_parameters.append({'ParameterKey': p, 'ParameterValue': value})

                    # Hide NoEcho parameters in shell output
                    if 'NoEcho' in self.cfn_data['Parameters'][p] and self.cfn_data['Parameters'][p]['NoEcho']:
                        value = '****'

                    _found[p] = value
                else:
                    # If we have a Default defined in the CFN skip, as AWS will use it
                    if 'Default' not in self.cfn_data['Parameters'][p]:
                        _errors.append(p)

            if _errors:
                raise ParameterNotFound('Cannot find value for parameters: {0}'.format(_errors))

            logger.info('{} {} set parameters:\n{}'.format(self.region, self.stackname, pprint.pformat(_found, indent=2)))

        # Return dict of explicitly set parameters
        return _found

    @exec_hooks
    def create(self):
        """Creates a stack """

        # Prepare parameters
        self.resolve_parameters()

        logger.info('Creating {0} {1}'.format(self.region, self.stackname))
        self.aws_stackid = self.connection_manager.call(
            'cloudformation', 'create_stack',
            {'StackName': self.stackname,
                'TemplateBody': self.cfn_template,
                'Parameters': self.cfn_parameters,
                'OnFailure': self.onfailure,
                'NotificationARNs': self.notfication_sns,
                'Tags': [{"Key": str(k), "Value": str(v)} for k, v in self.tags.items()],
                'Capabilities': ['CAPABILITY_IAM', 'CAPABILITY_NAMED_IAM', 'CAPABILITY_AUTO_EXPAND']},
            profile=self.profile, region=self.region)

        status = self._wait_for_completion()
        self.get_outputs()

        return status

    @exec_hooks
    def update(self):
        """Updates an existing stack """

        # Prepare parameters
        self.resolve_parameters()

        logger.info('Updating {0} {1}'.format(self.region, self.stackname))
        try:
            self.aws_stackid = self.connection_manager.call(
                'cloudformation', 'update_stack',
                {'StackName': self.stackname,
                    'TemplateBody': self.cfn_template,
                    'Parameters': self.cfn_parameters,
                    'NotificationARNs': self.notfication_sns,
                    'Tags': [{"Key": str(k), "Value": str(v)} for k, v in self.tags.items()],
                    'Capabilities': ['CAPABILITY_IAM', 'CAPABILITY_NAMED_IAM', 'CAPABILITY_AUTO_EXPAND']},
                profile=self.profile, region=self.region)

        except ClientError as e:
            if 'No updates are to be performed' in e.response['Error']['Message']:
                logger.info('No updates for {0}'.format(self.stackname))
                return StackStatus.COMPLETE
            else:
                raise e

        status = self._wait_for_completion()
        self.get_outputs()

        return status

    @exec_hooks
    def delete(self):
        """Deletes a stack """

        logger.info('Deleting {0} {1}'.format(self.region, self.stackname))
        self.aws_stackid = self.connection_manager.call(
            'cloudformation', 'delete_stack', {'StackName': self.stackname},
            profile=self.profile, region=self.region)

        status = self._wait_for_completion()
        return status

    def create_change_set(self, change_set_name):
        """ Creates a Change Set with the name ``change_set_name``.  """

        # Prepare parameters
        self.resolve_parameters()
        self.read_template_file()

        logger.info('Creating change set {0} for stack {1}'.format(change_set_name, self.stackname))
        self.connection_manager.call(
            'cloudformation', 'create_change_set',
            {'StackName': self.stackname,
                'ChangeSetName': change_set_name,
                'TemplateBody': self.cfn_template,
                'Parameters': self.cfn_parameters,
                'Tags': [{"Key": str(k), "Value": str(v)} for k, v in self.tags.items()],
                'Capabilities': ['CAPABILITY_IAM', 'CAPABILITY_NAMED_IAM']},
            profile=self.profile, region=self.region)
        return self._wait_for_completion()

    def describe(self):
        """
        Returns the a description of the stack.
        :returns: A stack description.
        """
        return self.connection_manager.call(
            "cloudformation",
            "describe_stacks",
            {"StackName": self.stackname},
            profile=self.profile, region=self.region)

    def get_status(self):
        """
        Returns the stack's status.
        :returns: The stack's status.
        """
        try:
            status = self.describe()["Stacks"][0]["StackStatus"]
        except ClientError as e:
            if e.response["Error"]["Message"].endswith("does not exist"):
                return None
            else:
                raise e
        return status

    def describe_events(self):
        """
        Returns a dictionary contianing the stack events.
        :returns: The CloudFormation events for a stack.
        """
        try:
            status = self.connection_manager.call(
                "cloudformation",
                "describe_stack_events",
                {"StackName": self.stackname},
                profile=self.profile, region=self.region)
        except ClientError as e:
            if e.response["Error"]["Message"].endswith("does not exist"):
                return None
            else:
                raise e

        return status

    def _wait_for_completion(self, timeout=0):
        """
        Waits for a stack operation to finish. Prints CloudFormation events while it waits.
        :param timeout: Timeout before returning
        :returns: The final stack status.
        """

        def timed_out(elapsed):
            return elapsed >= timeout if timeout else False

        status = StackStatus.IN_PROGRESS

        self.most_recent_event_datetime = (
            datetime.now(tzutc()) - timedelta(seconds=3)
        )
        elapsed = 0
        while status == StackStatus.IN_PROGRESS and not timed_out(elapsed):
            status = self._get_simplified_status(self.get_status())
            if not status:
                return None

            self._log_new_events()
            time.sleep(4)
            elapsed += 4

        return status

    @staticmethod
    def _get_simplified_status(status):
        """ Returns the simplified Stack Status.  """
        if status:
            if status.endswith("ROLLBACK_COMPLETE"):
                return StackStatus.FAILED
            elif status.endswith("_COMPLETE"):
                return StackStatus.COMPLETE
            elif status.endswith("_IN_PROGRESS"):
                return StackStatus.IN_PROGRESS
            elif status.endswith("_FAILED"):
                return StackStatus.FAILED
            else:
                return 'Unknown'

    def _log_new_events(self):
        """
        Log the latest stack events while the stack is being built.
        """
        events = self.describe_events()
        if events:
            events = events["StackEvents"]
            events.reverse()
            new_events = [
                event for event in events
                if event["Timestamp"] > self.most_recent_event_datetime
            ]
            for event in new_events:
                logger.info(" ".join([
                    self.region,
                    self.stackname,
                    event["LogicalResourceId"],
                    event["ResourceType"],
                    event["ResourceStatus"],
                    event.get("ResourceStatusReason", "")
                ]))
                self.most_recent_event_datetime = event["Timestamp"]

    # stackoutput inspection
    def _inspect_stacks(self, conglomerate):
        # Get all stacks of the conglomertate
        running_stacks = self.connection_manager.call(
            "cloudformation",
            "describe_stacks",
            profile=self.profile, region=self.region)

        stacks = []
        for stack in running_stacks['Stacks']:
            for tag in stack['Tags']:
                if tag['Key'] == 'Conglomerate' and tag['Value'] == conglomerate:
                    stacks.append(stack)
                    break

        # Gather stack outputs, use Tag['Artifact'] as name space: Artifact.OutputName, same as FortyTwo
        stack_outputs = {}
        for stack in stacks:
            # If stack has an Artifact Tag put resources into the namespace Artifact.Resource
            artifact = None
            for tag in stack['Tags']:
                if tag['Key'] == 'Artifact':
                    artifact = tag['Value']

            if artifact:
                key_prefix = "{}.".format(artifact)
            else:
                key_prefix = ""

            try:
                for output in stack['Outputs']:
                    # Gather all outputs of the stack into one dimensional key=value structure
                    stack_outputs[key_prefix + output['OutputKey']] = output['OutputValue']
            except KeyError:
                pass

        # Add outputs from stacks into the data for jinja under StackOutput
        return stack_outputs
