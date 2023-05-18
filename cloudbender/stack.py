import os
import re
import hashlib
import json
import yaml
import time
import pathlib
import pprint
import pulumi
import importlib
import pkg_resources

from datetime import datetime, timedelta
from dateutil.tz import tzutc

from botocore.exceptions import ClientError

from .utils import dict_merge, search_refs, ensure_dir, get_s3_url
from .connection import BotoConnection
from .jinja import JinjaEnv, read_config_file, render_docs
from . import __version__
from .exceptions import ParameterNotFound, ParameterIllegalValue, ChecksumError
from .hooks import exec_hooks
from .pulumi import pulumi_ws, resolve_outputs

import cfnlint.core
import cfnlint.template

from . import templates

import logging

logger = logging.getLogger(__name__)


# Ignore any !<Constructors> during re-loading of CFN templates
class SafeLoaderIgnoreUnknown(yaml.SafeLoader):
    def ignore_unknown(self, node):
        return node.tag


SafeLoaderIgnoreUnknown.add_constructor(None, SafeLoaderIgnoreUnknown.ignore_unknown)


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
        self.options = {}
        self.region = "global"
        self.profile = None
        self.onfailure = "DELETE"
        self.notfication_sns = []

        self.aws_stackid = None

        self.md5 = None
        self.mode = "CloudBender"
        self.provides = template
        self.cfn_template = None
        self.cfn_parameters = []
        self.cfn_data = None
        self.connection_manager = None
        self.status = None
        self.store_outputs = False
        self.dependencies = set()
        self.hooks = {
            "post_create": [],
            "post_update": [],
            "pre_create": [],
            "pre_update": [],
        }
        self.default_lock = None
        self.multi_delete = True
        self.template_bucket_url = None

        self.work_dir = None
        self.pulumi = {}
        self._pulumi_stack = None
        self.pulumi_stackname = ""
        self.pulumi_config = {}
        self.pulumi_ws_opts = None

    def dump_config(self):
        logger.debug("<Stack {}: {}>".format(self.id, pprint.pformat(vars(self))))

    def read_config(self, sg_config={}):
        """reads stack config"""

        # First set various attributes based on parent stackgroup config
        self.tags.update(sg_config.get("tags", {}))
        self.parameters.update(sg_config.get("parameters", {}))
        self.options.update(sg_config.get("options", {}))
        self.pulumi.update(sg_config.get("pulumi", {}))

        # by default inherit parent group settings
        for p in ["region", "notfication_sns", "template_bucket_url"]:
            if p in sg_config:
                setattr(self, p, sg_config[p])

        # profile and region need special treatment due to cmd line overwrite option
        if self.ctx["region"]:
            self.region = self.ctx["region"]

        if self.ctx["profile"]:
            self.profile = self.ctx["profile"]
        else:
            if "profile" in sg_config:
                self.profile = sg_config["profile"]
            else:
                self.profile = "default"

        # now override stack specific settings
        _config = read_config_file(self.path, sg_config.get("variables", {}))
        for p in [
            "region",
            "stackname",
            "template",
            "default_lock",
            "multi_delete",
            "provides",
            "onfailure",
            "notification_sns",
            "template_bucket_url",
        ]:
            if p in _config:
                setattr(self, p, _config[p])

        for p in ["parameters", "tags", "pulumi"]:
            if p in _config:
                setattr(self, p, dict_merge(getattr(self, p), _config[p]))

        # Inject Artifact if not explicitly set
        if "Artifact" not in self.tags:
            self.tags["Artifact"] = self.provides

        if "options" in _config:
            self.options = dict_merge(self.options, _config["options"])

        if "Mode" in self.options:
            self.mode = self.options["Mode"]

        if "StoreOutputs" in self.options and self.options["StoreOutputs"]:
            self.store_outputs = True

        if "dependencies" in _config:
            for dep in _config["dependencies"]:
                self.dependencies.add(dep)

        # Some sanity checks
        if self.onfailure not in ["DO_NOTHING", "ROLLBACK", "DELETE"]:
            raise ParameterIllegalValue(
                "onfailure must be one of DO_NOTHING | ROLLBACK | DELETE"
            )

        self.id = (self.profile, self.region, self.stackname)
        self.connection_manager = BotoConnection(self.profile, self.region)

        logger.debug("Stack {} added.".format(self.id))

    def render(self):
        """Renders the cfn jinja template for this stack"""

        template_metadata = {
            "Template.Name": self.template,
            "Template.Hash": "__HASH__",
            "CloudBender.Version": __version__,
        }
        _config = {
            "mode": self.mode,
            "options": self.options,
            "metadata": template_metadata,
        }

        jenv = JinjaEnv(self.ctx["artifact_paths"])
        jenv.globals["_config"] = _config

        template = jenv.get_template("{0}{1}".format(self.template, ".yaml.jinja"))

        logger.info("Rendering %s", template.filename)

        try:
            self.cfn_template = template.render(_config)
            self.cfn_data = yaml.load(self.cfn_template, Loader=SafeLoaderIgnoreUnknown)
        except Exception as e:
            # In case we rendered invalid yaml this helps to debug
            if self.cfn_template:
                _output = ""
                for i, line in enumerate(self.cfn_template.splitlines(), start=1):
                    _output = _output + "{}: {}\n".format(i, line)
                logger.error(_output)
            raise e

        if not re.search("CloudBender::", self.cfn_template) and not re.search(
            "Iterate:", self.cfn_template
        ):
            logger.info(
                "CloudBender not required -> removing Transform and Conglomerate parameter"
            )
            self.cfn_template = self.cfn_template.replace(
                "Transform: [CloudBender]", ""
            )

            _res = """
  Conglomerate:
    Type: String
    Description: Project / Namespace this stack is part of
"""
            self.cfn_template = re.sub(_res, "", self.cfn_template)
        else:
            self.dependencies.add("CloudBender")

        include = []
        search_refs(self.cfn_data, include, self.mode)
        if self.mode == "Piped" and len(include):
            _res = ""
            for attr in include:
                _res = (
                    _res
                    + """
  {0}:
    Type: String
    Description: Parameter to provide remote stack attribute {0}""".format(
                        attr
                    )
                )

            self.cfn_template = re.sub(
                r"Parameters:", r"Parameters:" + _res + "\n", self.cfn_template
            )
            logger.info("Piped mode: Added parameters for remote stack references")

        # Re-read updated template
        self.cfn_data = yaml.load(self.cfn_template, Loader=SafeLoaderIgnoreUnknown)

        # Check for empty top level Parameters, Outputs and Conditions and remove
        for key in ["Parameters", "Outputs", "Conditions"]:
            if key in self.cfn_data and not self.cfn_data[key]:
                del self.cfn_data[key]
                self.cfn_template = self.cfn_template.replace("\n" + key + ":", "")

        # Remove and condense multiple empty lines
        self.cfn_template = re.sub(r"\n\s*\n", "\n\n", self.cfn_template)
        self.cfn_template = re.sub(r"^\s*", "", self.cfn_template)
        self.cfn_template = re.sub(r"\s*$", "", self.cfn_template)

        # set md5 last
        self.md5 = hashlib.md5(self.cfn_template.encode("utf-8")).hexdigest()
        self.cfn_template = self.cfn_template.replace("__HASH__", self.md5)

        # Update internal data structures
        self._parse_metadata()

    def _parse_metadata(self):
        # Extract dependencies
        try:
            for dep in self.cfn_data["Metadata"]["CloudBender"]["Dependencies"]:
                self.dependencies.add(dep)
        except KeyError:
            pass

        # Get checksum
        if not self.md5:
            try:
                self.md5 = self.cfn_data["Metadata"]["Template"]["Hash"]

                # Verify embedded md5 hash
                source_cfn = re.sub(
                    "Hash: [0-9a-f]{32}", "Hash: __HASH__", self.cfn_template
                )
                our_md5 = hashlib.md5(source_cfn.encode("utf-8")).hexdigest()
                if our_md5 != self.md5:
                    raise ChecksumError(
                        "Template hash checksum mismatch! Expected: {} Got: {}".format(
                            self.md5, our_md5
                        )
                    ) from None

            except KeyError:
                raise ChecksumError("Template missing Hash checksum!") from None

        # Add CloudBender dependencies
        include = []
        search_refs(self.cfn_data, include, self.mode)
        for ref in include:
            if self.mode != "Piped":
                self.dependencies.add(ref.split(".")[0])
            else:
                self.dependencies.add(ref.split("DoT")[0])

        # Extract hooks
        try:
            for hook, func in self.cfn_data["Metadata"]["Hooks"].items():
                if hook in ["post_update", "post_create", "pre_create", "pre_update"]:
                    if isinstance(func, list):
                        self.hooks[hook].extend(func)
                    else:
                        self.hooks[hook].append(func)
        except KeyError:
            pass

    def write_template_file(self):
        if self.cfn_template:
            yaml_file = os.path.join(
                self.ctx["template_path"], self.rel_path, self.stackname + ".yaml"
            )
            ensure_dir(os.path.join(self.ctx["template_path"], self.rel_path))
            with open(yaml_file, "w") as yaml_contents:
                yaml_contents.write(self.cfn_template)
                logger.info("Wrote %s to %s", self.template, yaml_file)

            # upload template to s3 if set
            if self.template_bucket_url:
                try:
                    (bucket, path) = get_s3_url(
                        self.template_bucket_url,
                        self.rel_path,
                        self.stackname + ".yaml",
                    )
                    self.connection_manager.call(
                        "s3",
                        "put_object",
                        {
                            "Bucket": bucket,
                            "Key": path,
                            "Body": self.cfn_template,
                            "ServerSideEncryption": "AES256",
                        },
                        profile=self.profile,
                        region=self.region,
                    )

                    logger.info("Uploaded template to s3://{}/{}".format(bucket, path))
                except ClientError as e:
                    logger.error(
                        "Error trying to upload template so S3: {}, {}".format(
                            self.template_bucket_url, e
                        )
                    )

            else:
                if len(self.cfn_template) > 51200:
                    logger.warning(
                        "template_bucket_url not set and rendered template exceeds maximum allowed size of 51200, actual size: {} !".format(
                            len(self.cfn_template)
                        )
                    )
        else:
            logger.error(
                "No cfn template rendered yet for stack {}.".format(self.stackname)
            )

    def delete_template_file(self):
        yaml_file = os.path.join(
            self.ctx["template_path"], self.rel_path, self.stackname + ".yaml"
        )
        try:
            os.remove(yaml_file)
            logger.debug("Deleted cfn template %s.", yaml_file)
        except OSError:
            pass

        if self.template_bucket_url:
            try:
                (bucket, path) = get_s3_url(
                    self.template_bucket_url, self.rel_path, self.stackname + ".yaml"
                )
                self.connection_manager.call(
                    "s3",
                    "delete_object",
                    {"Bucket": bucket, "Key": path},
                    profile=self.profile,
                    region=self.region,
                )

                logger.info("Deleted template from s3://{}/{}".format(bucket, path))
            except ClientError as e:
                logger.error(
                    "Error trying to delete template from S3: {}, {}".format(
                        self.template_bucket_url, e
                    )
                )

    def read_template_file(self):
        """Reads rendered yaml template from disk or s3 and extracts metadata"""
        if not self.cfn_template:
            if self.template_bucket_url:
                try:
                    (bucket, path) = get_s3_url(
                        self.template_bucket_url,
                        self.rel_path,
                        self.stackname + ".yaml",
                    )
                    template = self.connection_manager.call(
                        "s3",
                        "get_object",
                        {"Bucket": bucket, "Key": path},
                        profile=self.profile,
                        region=self.region,
                    )
                    logger.debug("Got template from s3://{}/{}".format(bucket, path))

                    self.cfn_template = template["Body"].read().decode("utf-8")

                    # Overwrite local copy
                    yaml_file = os.path.join(
                        self.ctx["template_path"],
                        self.rel_path,
                        self.stackname + ".yaml",
                    )
                    ensure_dir(os.path.join(self.ctx["template_path"], self.rel_path))
                    with open(yaml_file, "w") as yaml_contents:
                        yaml_contents.write(self.cfn_template)

                except ClientError as e:
                    logger.error(
                        "Could not find template file on S3: {}/{}, {}".format(
                            bucket, path, e
                        )
                    )

            else:
                yaml_file = os.path.join(
                    self.ctx["template_path"], self.rel_path, self.stackname + ".yaml"
                )

                try:
                    with open(yaml_file, "r") as yaml_contents:
                        self.cfn_template = yaml_contents.read()
                        logger.debug("Read cfn template %s.", yaml_file)
                except FileNotFoundError as e:
                    logger.warn("Could not find template file: {}".format(yaml_file))
                    raise e

            self.cfn_data = yaml.load(self.cfn_template, Loader=SafeLoaderIgnoreUnknown)
            self._parse_metadata()

        else:
            logger.debug("Using cached cfn template %s.", self.stackname)

    def validate(self):
        """Validates the rendered template via cfn-lint"""
        self.read_template_file()

        try:
            ignore_checks = self.cfn_data["Metadata"]["cfnlint_ignore"]
        except KeyError:
            ignore_checks = []

        # Ignore some more checks around injected parameters as we generate these
        if self.mode == "Piped":
            ignore_checks = ignore_checks + ["W2505", "W2509", "W2507"]

        # Ignore checks regarding overloaded properties
        if self.mode == "CloudBender":
            ignore_checks = ignore_checks + [
                "E3035",
                "E3002",
                "E3012",
                "W2001",
                "E3001",
                "E0002",
                "E1012",
            ]

        filename = os.path.join(
            self.ctx["template_path"], self.rel_path, self.stackname + ".yaml"
        )
        logger.info("Validating {0}".format(filename))

        lint_args = ["--template", filename]
        if ignore_checks:
            lint_args.append("--ignore-checks")
            lint_args = lint_args + ignore_checks
            logger.info("Ignoring checks: {}".format(",".join(ignore_checks)))

        (args, filenames, formatter) = cfnlint.core.get_args_filenames(lint_args)
        (template, rules, matches) = cfnlint.core.get_template_rules(filename, args)

        region = self.region
        if region == "global":
            region = "us-east-1"

        if not matches:
            matches.extend(cfnlint.core.run_checks(filename, template, rules, [region]))
        if len(matches):
            for match in matches:
                logger.error(formatter._format(match))
            return 1
        else:
            logger.info("Passed.")
            return 0

    @pulumi_ws
    def get_outputs(self, include=".*", values=False):
        """gets outputs of the stack"""

        if self.mode == "pulumi":
            self.outputs = self._get_pulumi_stack().outputs()

        else:
            self.read_template_file()
            try:
                stacks = self.connection_manager.call(
                    "cloudformation",
                    "describe_stacks",
                    {"StackName": self.stackname},
                    profile=self.profile,
                    region=self.region,
                )["Stacks"]

                try:
                    for output in stacks[0]["Outputs"]:
                        self.outputs[output["OutputKey"]] = output["OutputValue"]
                    logger.debug(
                        "Stack outputs for {} in {}: {}".format(
                            self.stackname, self.region, self.outputs
                        )
                    )
                except KeyError:
                    pass

            except ClientError:
                logger.warn("Could not get outputs of {}".format(self.stackname))
                pass

        if self.outputs:
            if self.store_outputs:
                filename = self.stackname + ".yaml"
                my_template = importlib.resources.read_text(templates, "outputs.yaml")

                output_file = os.path.join(
                    self.ctx["outputs_path"], self.rel_path, filename
                )
                ensure_dir(os.path.join(self.ctx["outputs_path"], self.rel_path))

                # Blacklist at least AWS SecretKeys from leaking into git
                # Pulumi to the rescue soon
                blacklist = [".*SecretAccessKey.*"]
                sanitized_outputs = {}
                for k in self.outputs.keys():
                    sanitized_outputs[k] = self.outputs[k]
                    for val in blacklist:
                        if re.match(val, k, re.IGNORECASE):
                            sanitized_outputs[k] = "<Redacted>"

                jenv = JinjaEnv()
                template = jenv.from_string(my_template)
                data = {
                    "stackname": "/".join([self.rel_path, self.stackname]),
                    "timestamp": datetime.strftime(
                        datetime.now(tzutc()), "%d/%m/%y %H:%M"
                    ),
                    "outputs": sanitized_outputs,
                    "parameters": self.parameters,
                }

                with open(output_file, "w") as output_contents:
                    output_contents.write(template.render(**data))
                    logger.info(
                        "Wrote outputs for %s to %s", self.stackname, output_file
                    )

            # If secrets replace with clear values for now, display ONLY
            for k in self.outputs.keys():
                if hasattr(self.outputs[k], "secret") and self.outputs[k].secret:
                    self.outputs[k] = self.outputs[k].value

            logger.info(
                "{} {} Outputs:\n{}".format(
                    self.region, self.stackname, pprint.pformat(self.outputs, indent=2)
                )
            )

    @pulumi_ws
    def docs(self, template=False):
        """Read rendered template, parse documentation fragments, eg. parameter description
        and create a mardown doc file for the stack. Same idea as helm-docs for the values.yaml
        """

        doc_file = os.path.join(
            self.ctx["docs_path"], self.rel_path, self.stackname + ".md"
        )
        ensure_dir(os.path.join(self.ctx["docs_path"], self.rel_path))

        # For pulumi we use the embedded docstrings
        if self.mode == "pulumi":
            try:
                pulumi_stack = self._get_pulumi_stack()
                outputs = pulumi_stack.outputs()
            except pulumi.automation.errors.StackNotFoundError:
                outputs = {}
                pass

            if vars(self._pulumi_code)["__doc__"]:
                docs_out = render_docs(
                    vars(self._pulumi_code)["__doc__"], resolve_outputs(outputs)
                )
            else:
                docs_out = "No stack documentation available."

            # collect all __doc__ from available _execute_ functions
            headerAdded = False
            for k in vars(self._pulumi_code).keys():
                if k.startswith("_execute_"):
                    if not headerAdded:
                        docs_out = docs_out + "\n# Available *execute* functions:  \n"
                        headerAdded = True
                    docstring = vars(self._pulumi_code)[k].__doc__
                    docs_out = docs_out + f"\n* {docstring}"

        # Cloudformation we use the stack-doc template similar to helm-docs
        else:
            try:
                self.read_template_file()
            except FileNotFoundError:
                return

            if not template:
                doc_template = importlib.resources.read_text(templates, "stack-doc.md")
                jenv = JinjaEnv()
                template = jenv.from_string(doc_template)
                data = {}
            else:
                doc_template = template

            data["name"] = self.stackname
            data["description"] = self.cfn_data["Description"]
            data["dependencies"] = self.dependencies

            if "Parameters" in self.cfn_data:
                data["parameters"] = self.cfn_data["Parameters"]
                set_parameters = self.resolve_parameters()
                for p in set_parameters:
                    data["parameters"][p]["value"] = set_parameters[p]

            if "Outputs" in self.cfn_data:
                data["outputs"] = self.cfn_data["Outputs"]

                # Check for existing outputs yaml, if found add current value column and set header to timestamp from outputs file
                output_file = os.path.join(
                    self.ctx["outputs_path"], self.rel_path, self.stackname + ".yaml"
                )

                try:
                    with open(output_file, "r") as yaml_contents:
                        outputs = yaml.safe_load(yaml_contents.read())
                        for p in outputs["Outputs"]:
                            data["outputs"][p]["last_value"] = outputs["Outputs"][p]
                        data["timestamp"] = outputs["TimeStamp"]
                except (FileNotFoundError, KeyError, TypeError):
                    pass

            docs_out = template.render(**data)

        # Finally write docs to file
        with open(doc_file, "w") as doc_contents:
            doc_contents.write(docs_out)
            logger.info("Wrote documentation for %s to %s", self.stackname, doc_file)

    def resolve_parameters(self):
        """Renders parameters for the stack based on the source template and the environment configuration"""

        self.read_template_file()

        # if we run in Piped Mode, inspect all outputs of the running Conglomerate members
        if self.mode == "Piped":
            stack_outputs = {}
            try:
                stack_outputs = self._inspect_stacks(self.tags["Conglomerate"])
            except KeyError:
                pass

        _found = {}
        if "Parameters" in self.cfn_data:
            _errors = []
            self.cfn_parameters = []
            for p in self.cfn_data["Parameters"]:
                # In Piped mode we try to resolve all Paramters first via stack_outputs
                if self.mode == "Piped":
                    try:
                        # first reverse the rename due to AWS alphanumeric restriction for parameter names
                        _p = p.replace("DoT", ".")
                        value = str(stack_outputs[_p])
                        self.cfn_parameters.append(
                            {"ParameterKey": p, "ParameterValue": value}
                        )
                        logger.info("Got {} = {} from running stack".format(p, value))
                        continue
                    except KeyError:
                        pass

                # Key name in config tree is: stacks.<self.stackname>.parameters.<parameter>
                if p in self.parameters:
                    value = str(self.parameters[p])
                    self.cfn_parameters.append(
                        {"ParameterKey": p, "ParameterValue": value}
                    )

                    # Hide NoEcho parameters in shell output
                    if (
                        "NoEcho" in self.cfn_data["Parameters"][p]
                        and self.cfn_data["Parameters"][p]["NoEcho"]
                    ):
                        value = "****"

                    _found[p] = value
                else:
                    # If we have a Default defined in the CFN skip, as AWS will use it
                    if "Default" not in self.cfn_data["Parameters"][p]:
                        _errors.append(p)

            if _errors:
                raise ParameterNotFound(
                    "Cannot find value for parameters: {0}".format(_errors)
                )

            # Warning of excessive parameters, might be useful to spot typos early
            _warnings = []
            for p in self.parameters.keys():
                if p not in self.cfn_data["Parameters"]:
                    _warnings.append(p)

            logger.info(
                "{} {} set parameters:\n{}".format(
                    self.region, self.stackname, pprint.pformat(_found, indent=2)
                )
            )

            if _warnings:
                logger.warning("Ignored additional parameters: {}.".format(_warnings))

        # Return dict of explicitly set parameters
        return _found

    @pulumi_ws
    @exec_hooks
    def create(self):
        """Creates a stack"""

        if self.mode == "pulumi":
            kwargs = self._set_pulumi_args()
            self._get_pulumi_stack(create=True).up(**kwargs)

        else:
            # Prepare parameters
            self.resolve_parameters()

            logger.info("Creating {0} {1}".format(self.region, self.stackname))
            kwargs = {
                "StackName": self.stackname,
                "Parameters": self.cfn_parameters,
                "OnFailure": self.onfailure,
                "NotificationARNs": self.notfication_sns,
                "Tags": [
                    {"Key": str(k), "Value": str(v)} for k, v in self.tags.items()
                ],
                "Capabilities": [
                    "CAPABILITY_IAM",
                    "CAPABILITY_NAMED_IAM",
                    "CAPABILITY_AUTO_EXPAND",
                ],
            }
            kwargs = self._add_template_arg(kwargs)

            self.aws_stackid = self.connection_manager.call(
                "cloudformation",
                "create_stack",
                kwargs,
                profile=self.profile,
                region=self.region,
            )

            status = self._wait_for_completion()
            self.get_outputs()

            return status

    @exec_hooks
    def update(self):
        """Updates an existing stack"""

        # Prepare parameters
        self.resolve_parameters()

        logger.info("Updating {0} {1}".format(self.region, self.stackname))
        try:
            kwargs = {
                "StackName": self.stackname,
                "Parameters": self.cfn_parameters,
                "NotificationARNs": self.notfication_sns,
                "Tags": [
                    {"Key": str(k), "Value": str(v)} for k, v in self.tags.items()
                ],
                "Capabilities": [
                    "CAPABILITY_IAM",
                    "CAPABILITY_NAMED_IAM",
                    "CAPABILITY_AUTO_EXPAND",
                ],
            }
            kwargs = self._add_template_arg(kwargs)

            self.aws_stackid = self.connection_manager.call(
                "cloudformation",
                "update_stack",
                kwargs,
                profile=self.profile,
                region=self.region,
            )

        except ClientError as e:
            if "No updates are to be performed" in e.response["Error"]["Message"]:
                logger.info("No updates for {0}".format(self.stackname))
                return "COMPLETE"
            else:
                raise e

        status = self._wait_for_completion()
        self.get_outputs()

        return status

    @pulumi_ws
    @exec_hooks
    def delete(self):
        """Deletes a stack"""

        logger.info("Deleting {0} {1}".format(self.region, self.stackname))

        if self.mode == "pulumi":
            try:
                pulumi_stack = self._get_pulumi_stack()
            except pulumi.automation.errors.StackNotFoundError:
                logger.warning("Could not find Pulumi stack {}".format(self.stackname))
                return

            pulumi_stack.destroy(on_output=self._log_pulumi)
            pulumi_stack.workspace.remove_stack(pulumi_stack.name)

            return

        self.aws_stackid = self.connection_manager.call(
            "cloudformation",
            "delete_stack",
            {"StackName": self.stackname},
            profile=self.profile,
            region=self.region,
        )

        status = self._wait_for_completion()
        return status

    @pulumi_ws
    def refresh(self):
        """Refreshes a Pulumi stack"""

        self._get_pulumi_stack().refresh(on_output=self._log_pulumi)

        return

    @pulumi_ws
    def preview(self):
        """Preview a Pulumi stack up operation"""

        kwargs = self._set_pulumi_args()
        self._get_pulumi_stack(create=True).preview(**kwargs)

        return

    @pulumi_ws
    def execute(self, function, args):
        """
        Executes custom Python function within a Pulumi stack

        These plugin functions are executed within the stack environment and are provided with all stack input parameters as well as current outputs.
        Think of "docker exec" into an existing container...

        """
        if not function:
            logger.error("No function specified !")
            headerAdded = False
            for k in vars(self._pulumi_code).keys():
                if k.startswith("_execute_"):
                    if not headerAdded:
                        logger.info("Available execute functions:")
                        headerAdded = True
                    logger.info("{}".format(k.replace("_execute_", "- ")))

            return

        exec_function = f"_execute_{function}"
        if exec_function in vars(self._pulumi_code):
            pulumi_stack = self._get_pulumi_stack()

            try:
                vars(self._pulumi_code)[exec_function](
                    config=pulumi_stack.get_all_config(),
                    outputs=pulumi_stack.outputs(),
                    args=args,
                )
            except Exception as e:
                return e.returncode

        else:
            logger.error("{} is not defined in {}".format(function, self._pulumi_code))

    @pulumi_ws
    def assimilate(self):
        """Import resources into Pulumi stack"""

        pulumi_stack = self._get_pulumi_stack(create=True)

        # now lets import each defined resource
        for r in self._pulumi_code.RESOURCES:
            r_id = r["id"]
            if not r_id:
                r_id = input(
                    "Please enter ID for {} ({}):".format(r["name"], r["type"])
                )

            logger.info("Importing {} ({}) as {}".format(r_id, r["type"], r["name"]))

            args = ["import", r["type"], r["name"], r_id, "--yes"]
            pulumi_stack._run_pulumi_cmd_sync(args)

        return

    @pulumi_ws
    def export(self, remove_pending_operations):
        """Exports a Pulumi stack"""

        pulumi_stack = self._get_pulumi_stack()
        deployment = pulumi_stack.export_stack()

        if remove_pending_operations:
            deployment.deployment.pop("pending_operations", None)
            pulumi_stack.import_stack(deployment)
            logger.info("Removed all pending_operations from %s" % self.stackname)
        else:
            print(json.dumps(deployment.deployment))

        return

    @pulumi_ws
    def set_config(self, key, value, secret):
        """Set a config or secret"""

        pulumi_stack = self._get_pulumi_stack(create=True)
        pulumi_stack.set_config(key, pulumi.automation.ConfigValue(value, secret))

        # Store salt or key and encrypted value in CloudBender stack config
        settings = None
        pulumi_settings = pulumi_stack.workspace.stack_settings(
            pulumi_stack.name
        )._serialize()

        with open(self.path, "r") as file:
            settings = yaml.safe_load(file)

            if "pulumi" not in settings:
                settings["pulumi"] = {}

            if "encryptionsalt" in pulumi_settings:
                settings["pulumi"]["encryptionsalt"] = pulumi_settings["encryptionsalt"]
            if "encryptedkey" in pulumi_settings:
                settings["pulumi"]["encryptedkey"] = pulumi_settings["encryptedkey"]

            if "parameters" not in settings:
                settings["parameters"] = {}
            settings["parameters"][key] = pulumi_settings["config"][
                "{}:{}".format(self.parameters["Conglomerate"], key)
            ]

        with open(self.path, "w") as file:
            yaml.dump(settings, stream=file)

        return

    @pulumi_ws
    def get_config(self, key):
        """Get a config or secret"""

        print(self._get_pulumi_stack().get_config(key).value)

    def create_change_set(self, change_set_name):
        """Creates a Change Set with the name ``change_set_name``."""

        # Prepare parameters
        self.resolve_parameters()
        self.read_template_file()

        logger.info(
            "Creating change set {0} for stack {1}".format(
                change_set_name, self.stackname
            )
        )
        kwargs = {
            "StackName": self.stackname,
            "ChangeSetName": change_set_name,
            "Parameters": self.cfn_parameters,
            "Tags": [{"Key": str(k), "Value": str(v)} for k, v in self.tags.items()],
            "Capabilities": ["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"],
        }
        kwargs = self._add_template_arg(kwargs)

        self.connection_manager.call(
            "cloudformation",
            "create_change_set",
            kwargs,
            profile=self.profile,
            region=self.region,
        )
        return self._wait_for_completion()

    def get_status(self):
        """
        Returns the stack's status.
        :returns: The stack's status.
        """
        try:
            status = self.connection_manager.call(
                "cloudformation",
                "describe_stacks",
                {"StackName": self.stackname},
                profile=self.profile,
                region=self.region,
            )["Stacks"][0]["StackStatus"]
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
                profile=self.profile,
                region=self.region,
            )
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

        status = "IN_PROGRESS"

        self.most_recent_event_datetime = datetime.now(tzutc()) - timedelta(seconds=3)
        elapsed = 0
        while status == "IN_PROGRESS" and not timed_out(elapsed):
            status = self._get_simplified_status(self.get_status())
            if not status:
                return None

            self._log_new_events()
            time.sleep(4)
            elapsed += 4

        return status

    @staticmethod
    def _get_simplified_status(status):
        """Returns the simplified Stack Status."""
        if status:
            if status.endswith("ROLLBACK_COMPLETE"):
                return "FAILED"
            elif status.endswith("_COMPLETE"):
                return "COMPLETE"
            elif status.endswith("_IN_PROGRESS"):
                return "IN_PROGRESS"
            elif status.endswith("_FAILED"):
                return "FAILED"
            else:
                return "Unknown"

    def _log_new_events(self):
        """
        Log the latest stack events while the stack is being built.
        """
        events = self.describe_events()
        if events:
            events = events["StackEvents"]
            events.reverse()
            new_events = [
                event
                for event in events
                if event["Timestamp"] > self.most_recent_event_datetime
            ]
            for event in new_events:
                logger.info(
                    " ".join(
                        [
                            self.region,
                            self.stackname,
                            event["LogicalResourceId"],
                            event["ResourceType"],
                            event["ResourceStatus"],
                            event.get("ResourceStatusReason", ""),
                        ]
                    )
                )
                self.most_recent_event_datetime = event["Timestamp"]

    # stackoutput inspection
    def _inspect_stacks(self, conglomerate):
        # Get all stacks of the conglomertate
        running_stacks = self.connection_manager.call(
            "cloudformation",
            "describe_stacks",
            profile=self.profile,
            region=self.region,
        )

        stacks = []
        for stack in running_stacks["Stacks"]:
            for tag in stack["Tags"]:
                if tag["Key"] == "Conglomerate" and tag["Value"] == conglomerate:
                    stacks.append(stack)
                    break

        # Gather stack outputs, use Tag['Artifact'] as name space: Artifact.OutputName
        stack_outputs = {}
        for stack in stacks:
            # If stack has an Artifact Tag put resources into the namespace Artifact.Resource
            artifact = None
            for tag in stack["Tags"]:
                if tag["Key"] == "Artifact":
                    artifact = tag["Value"]

            if artifact:
                key_prefix = "{}.".format(artifact)
            else:
                key_prefix = ""

            try:
                for output in stack["Outputs"]:
                    # Gather all outputs of the stack into one dimensional key=value structure
                    stack_outputs[key_prefix + output["OutputKey"]] = output[
                        "OutputValue"
                    ]
            except KeyError:
                pass

        # Add outputs from stacks into the data for jinja under StackOutput
        return stack_outputs

    def _add_template_arg(self, kwargs):
        if self.template_bucket_url:
            # https://bucket-name.s3.Region.amazonaws.com/key name
            # so we need the region, AWS as usual
            (bucket, path) = get_s3_url(
                self.template_bucket_url, self.rel_path, self.stackname + ".yaml"
            )
            bucket_region = self.connection_manager.call(
                "s3",
                "get_bucket_location",
                {"Bucket": bucket},
                profile=self.profile,
                region=self.region,
            )["LocationConstraint"]
            # If bucket is in us-east-1 AWS returns 'none' cause reasons grrr
            if not bucket_region:
                bucket_region = "us-east-1"

            kwargs["TemplateURL"] = "https://{}.s3.{}.amazonaws.com/{}".format(
                bucket, bucket_region, path
            )
        else:
            kwargs["TemplateBody"] = self.cfn_template

        return kwargs

    def _log_pulumi(self, text):
        text = re.sub(
            r"pulumi:pulumi:Stack\s*{}-{}\s*".format(
                self.parameters["Conglomerate"], self.stackname
            ),
            "",
            text,
        )
        if text and not text.isspace():
            logger.info(" ".join([self.region, self.stackname, text]))

    def _get_pulumi_stack(self, create=False):

        if create:
            pulumi_stack = pulumi.automation.create_or_select_stack(
                stack_name=self.pulumi_stackname,
                project_name=self.parameters["Conglomerate"],
                program=self._pulumi_code.pulumi_program,
                opts=self.pulumi_ws_opts,
            )
            pulumi_stack.workspace.install_plugin(
                "aws", pkg_resources.get_distribution("pulumi_aws").version
            )

        else:
            pulumi_stack = pulumi.automation.select_stack(
                stack_name=self.pulumi_stackname,
                project_name=self.parameters["Conglomerate"],
                program=self._pulumi_code.pulumi_program,
                opts=self.pulumi_ws_opts,
            )

        return pulumi_stack

    def _set_pulumi_args(self, kwargs={}):
        kwargs["on_output"] = self._log_pulumi
        kwargs["policy_packs"] = []
        kwargs["policy_pack_configs"] = []

        # Try to find policies in each artifact location
        if "policies" in self.pulumi:
            for policy in self.pulumi["policies"]:
                found = False
                for artifacts_path in self.ctx["artifact_paths"]:
                    path = "{}/pulumi/policies/{}".format(
                        artifacts_path.resolve(), policy
                    )
                    if os.path.exists(path):
                        kwargs["policy_packs"].append(path)
                        found = True
                if not found:
                    logger.error(f"Could not find policy implementation for {policy}!")
                    raise FileNotFoundError

        try:
            kwargs["policy_pack_configs"] = self.pulumi["policy_configs"]
        except KeyError:
            pass

        return kwargs
