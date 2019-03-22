import os
import re
import hashlib
import oyaml as yaml
import json
import time
import subprocess

from datetime import datetime, timedelta
from dateutil.tz import tzutc

from botocore.exceptions import ClientError

from .utils import dict_merge
from .connection import BotoConnection
from .jinja import JinjaEnv, read_config_file
from . import __version__

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
    def __init__(self, name, path, rel_path, tags=None, parameters=None, template_vars=None, region='global', profile=None, template=None, ctx={}):
        self.id = (profile, region, name)
        self.stackname = name
        self.path = path
        self.rel_path = rel_path
        self.tags = tags
        self.parameters = parameters
        self.template_vars = template_vars
        self.region = region
        self.profile = profile
        self.template = template
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

    def dump_config(self):
        logger.debug("<Stack {}: {}>".format(self.id, vars(self)))

    def read_config(self):
        _config = read_config_file(self.path)
        for p in ["region", "stackname", "template", "default_lock", "multi_delete", "provides"]:
            if p in _config:
                setattr(self, p, _config[p])

        for p in ["parameters", "tags"]:
            if p in _config:
                setattr(self, p, dict_merge(getattr(self, p), _config[p]))

        # Inject Artifact for now hard coded
        self.tags['Artifact'] = self.provides

        if 'vars' in _config:
            self.template_vars = dict_merge(self.template_vars, _config['vars'])

        if 'dependencies' in _config:
            for dep in _config['dependencies']:
                self.dependencies.add(dep)

        logger.debug("Stack {} added.".format(self.id))

    def render(self):
        """Renders the cfn jinja template for this stack"""

        jenv = JinjaEnv(self.ctx['artifact_paths'])

        template = jenv.get_template('{0}{1}'.format(self.template, '.yaml.jinja'))

        template_metadata = {
            'Template.Name': self.template,
            'Template.Hash': 'tbd',
            'CloudBender.Version': __version__
        }

        cb = False
        if self.template_vars['Mode'] == "CloudBender":
            cb = True

        _config = {'cb': cb, 'cfn': self.template_vars, 'Metadata': template_metadata}

        jenv.globals['_config'] = _config

        # First render pass to calculate a md5 checksum
        template_metadata['Template.Hash'] = hashlib.md5(template.render(_config).encode('utf-8')).hexdigest()

        # Reset and set Metadata for final render pass
        jenv.globals['get_custom_att'](context={'_config': self.template_vars}, reset=True)
        jenv.globals['render_once'](context={'_config': self.template_vars}, reset=True)
        jenv.globals['cloudbender_ctx'](context={'_config': self.template_vars}, reset=True)

        # Try to add latest tag/commit for the template source, skip if not in git tree
        try:
            _comment = subprocess.check_output('git log -1 --pretty=%B {}'.format(template.filename).split(' ')).decode('utf-8').strip().replace('"', '').replace('#', '').replace('\n', '').replace(':', ' ')
            if _comment:
                template_metadata['Template.LastGitComment'] = _comment

        except subprocess.CalledProcessError:
            pass

        logger.info('Rendering %s', template.filename)
        rendered = template.render(_config)

        try:
            self.data = yaml.safe_load(rendered)
        except Exception as e:
            # In case we rendered invalid yaml this helps to debug
            logger.error(rendered)
            raise e

        # Some sanity checks and final cosmetics
        # Check for empty top level Parameters, Outputs and Conditions and remove
        for key in ['Parameters', 'Outputs', 'Conditions']:
            if key in self.data and self.data[key] is None:
                # Delete from data structure which also takes care of json
                del self.data[key]
                # but also remove from rendered for the yaml file
                rendered = rendered.replace('\n' + key + ":", '')

        # Condense multiple empty lines to one
        self.cfn_template = re.sub(r'\n\s*\n', '\n\n', rendered)

        # Update internal data structures
        self._parse_metadata()

    def _parse_metadata(self):
        # Extract dependencies if present
        try:
            for dep in self.data['Metadata']['CloudBender']['Dependencies']:
                self.dependencies.add(dep)
        except KeyError:
            pass

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

            self.data = yaml.safe_load(self.cfn_template)
            self._parse_metadata()

        else:
            logger.debug('Using cached cfn template %s.', self.stackname)

    def validate(self):
        """Validates the rendered template via cfn-lint"""
        self.read_template_file()

        try:
            ignore_checks = self.data['Metadata']['cfnlint_ignore']
        except KeyError:
            ignore_checks = []

        # Ignore some more checks around injected parameters as we generate these
        if self.template_vars['Mode'] == "Piped":
            ignore_checks = ignore_checks + ['W2505', 'W2509', 'W2507']

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

        # Inspect all outputs of the running Conglomerate members
        # if we run in Piped Mode
        # if self.template_vars['Mode'] == "Piped":
        #     try:
        #         stack_outputs = inspect_stacks(config['tags']['Conglomerate'])
        #         logger.info(pprint.pformat(stack_outputs))
        #     except KeyError:
        #        pass

        if 'Parameters' in self.data:
            self.cfn_parameters = []
            for p in self.data['Parameters']:
                # In Piped mode we try to resolve all Paramters first via stack_outputs
                # if config['cfn']['Mode'] == "Piped":
                #    try:
                #        # first reverse the rename due to AWS alphanumeric restriction for parameter names
                #        _p = p.replace('DoT','.')
                #        value = str(stack_outputs[_p])
                #        parameters.append({'ParameterKey': p, 'ParameterValue': value })
                #        logger.info('Got {} = {} from running stack'.format(p,value))
                #        continue
                #    except KeyError:
                #        pass

                # Key name in config tree is: stacks.<self.stackname>.parameters.<parameter>
                try:
                    value = str(self.parameters[p])
                    self.cfn_parameters.append({'ParameterKey': p, 'ParameterValue': value})
                    logger.info('{} {} Parameter {}={}'.format(self.region, self.stackname, p, value))
                except KeyError:
                    # If we have a Default defined in the CFN skip, as AWS will use it
                    if 'Default' in self.data['Parameters'][p]:
                        continue
                    else:
                        logger.error('Cannot find value for parameter {0}'.format(p))

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
