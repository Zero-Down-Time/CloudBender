import os
import re
import hashlib
import oyaml as yaml
import json
import time

from datetime import datetime, timedelta
from dateutil.tz import tzutc

from botocore.exceptions import ClientError

from .utils import dict_merge, search_refs
from .connection import BotoConnection
from .jinja import JinjaEnv, read_config_file
from . import __version__
from .exceptions import ParameterNotFound, ParameterIllegalValue

import cfnlint.core

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
    def __init__(self, name, path, rel_path, tags=None, parameters=None, options=None, region='global', profile=None, template=None, ctx={}):
        self.id = (profile, region, name)
        self.stackname = name
        self.path = path
        self.rel_path = rel_path
        self.tags = tags
        self.parameters = parameters
        self.options = options
        self.region = region
        self.profile = profile
        self.template = template
        self.md5 = None
        self.mode = 'CloudBender'
        self.provides = template
        self.cfn_template = None
        self.cfn_parameters = []
        self.cfn_data = None
        self.connection_manager = BotoConnection(self.profile, self.region)
        self.ctx = ctx
        self.status = None
        self.dependencies = set()
        self.default_lock = None
        self.multi_delete = True
        self.onfailure = "DELETE"

    def dump_config(self):
        logger.debug("<Stack {}: {}>".format(self.id, vars(self)))

    def read_config(self):
        _config = read_config_file(self.path)
        for p in ["region", "stackname", "template", "default_lock", "multi_delete", "provides", "onfailure"]:
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
            self.options = dict_merge(self.options, _config['vars'])

        if 'options' in _config:
            self.options = dict_merge(self.options, _config['options'])

        if 'Mode' in self.options:
            self.mode = self.options['Mode']

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
                logger.error(self.cfn_template)
            raise e

        if not re.search('CloudBender::', self.cfn_template):
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
        if self.mode != "Piped" and len(include) and 'Legacy' in self.options:
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

    def write_template_file(self):
        if self.cfn_template:
            yaml_file = os.path.join(self.ctx['template_path'], self.rel_path, self.stackname + ".yaml")
            self._ensure_dirs('template_path')
            with open(yaml_file, 'w') as yaml_contents:
                yaml_contents.write(self.cfn_template)
                logger.info('Wrote %s to %s', self.template, yaml_file)

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
            with open(yaml_file, 'r') as yaml_contents:
                self.cfn_template = yaml_contents.read()
                logger.debug('Read cfn template %s.', yaml_file)

            self.cfn_data = yaml.safe_load(self.cfn_template)
            self._parse_metadata()

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
            ignore_checks = ignore_checks + ['E3035', 'E3002', 'E3012', 'W2001', 'E3001']

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
                    logger.info('{} {} Parameter {}={}'.format(self.region, self.stackname, p, value))
                else:
                    # If we have a Default defined in the CFN skip, as AWS will use it
                    if 'Default' not in self.cfn_data['Parameters'][p]:
                        _errors.append(p)

            if _errors:
                raise ParameterNotFound('Cannot find value for parameters: {0}'.format(_errors))

    def write_parameter_file(self):
        parameter_file = os.path.join(self.ctx['parameter_path'], self.rel_path, self.stackname + ".yaml")

        # Render parameters as json for AWS CFN
        self._ensure_dirs('parameter_path')
        with open(parameter_file, 'w') as parameter_contents:
            parameter_contents.write(json.dumps(self.cfn_parameters, indent=2, separators=(',', ': '), sort_keys=True))
            logger.info('Wrote json parameters for %s to %s', self.stackname, parameter_file)

        if not self.cfn_parameters:
            # Make sure there are no parameters from previous runs
            if os.path.isfile(parameter_file):
                os.remove(parameter_file)

    def delete_parameter_file(self):
        parameter_file = os.path.join(self.ctx['parameter_path'], self.rel_path, self.stackname + ".yaml")
        try:
            os.remove(parameter_file)
            logger.debug('Deleted parameter %s.', parameter_file)
        except OSError:
            pass

    def create(self):
        """Creates a stack """

        # Prepare parameters
        self.resolve_parameters()
        self.write_parameter_file()
        self.read_template_file()

        logger.info('Creating {0} {1}'.format(self.region, self.stackname))
        self.connection_manager.call(
            'cloudformation', 'create_stack',
            {'StackName': self.stackname,
                'TemplateBody': self.cfn_template,
                'Parameters': self.cfn_parameters,
                'OnFailure': self.onfailure,
                'Tags': [{"Key": str(k), "Value": str(v)} for k, v in self.tags.items()],
                'Capabilities': ['CAPABILITY_IAM', 'CAPABILITY_NAMED_IAM', 'CAPABILITY_AUTO_EXPAND']},
            profile=self.profile, region=self.region)

        return self._wait_for_completion()

    def update(self):
        """Updates an existing stack """

        # Prepare parameters
        self.resolve_parameters()
        self.write_parameter_file()
        self.read_template_file()

        logger.info('Updating {0} {1}'.format(self.region, self.stackname))
        try:
            self.connection_manager.call(
                'cloudformation', 'update_stack',
                {'StackName': self.stackname,
                    'TemplateBody': self.cfn_template,
                    'Parameters': self.cfn_parameters,
                    'Tags': [{"Key": str(k), "Value": str(v)} for k, v in self.tags.items()],
                    'Capabilities': ['CAPABILITY_IAM', 'CAPABILITY_NAMED_IAM', 'CAPABILITY_AUTO_EXPAND']},
                profile=self.profile, region=self.region)

        except ClientError as e:
            if 'No updates are to be performed' in e.response['Error']['Message']:
                logger.info('No updates for {0}'.format(self.stackname))
                return StackStatus.COMPLETE
            else:
                raise e

        return self._wait_for_completion()

    def delete(self):
        """Deletes a stack """

        logger.info('Deleting {0} {1}'.format(self.region, self.stackname))
        self.connection_manager.call(
            'cloudformation', 'delete_stack', {'StackName': self.stackname},
            profile=self.profile, region=self.region)

        return self._wait_for_completion()

    def create_change_set(self, change_set_name):
        """ Creates a Change Set with the name ``change_set_name``.  """

        # Prepare parameters
        self.resolve_parameters()
        self.write_parameter_file()
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

    def _ensure_dirs(self, path):
        # Ensure output dirs exist
        if not os.path.exists(os.path.join(self.ctx[path], self.rel_path)):
            os.makedirs(os.path.join(self.ctx[path], self.rel_path))

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
