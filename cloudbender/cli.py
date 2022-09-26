import os
import sys
import click
import functools
import re

from concurrent.futures import ThreadPoolExecutor, as_completed

from . import __version__
from .core import CloudBender
from .utils import setup_logging, get_docker_version
from .exceptions import InvalidProjectDir
from .pulumi import get_pulumi_version

import logging

logger = logging.getLogger(__name__)


@click.group()
@click.option(
    "--profile",
    "profile",
    help="Use named AWS .config profile, overwrites any stack config",
)
@click.option(
    "--region",
    "region",
    help="Use region, overwrites any stack config",
)
@click.option("--dir", "directory", help="Specify cloudbender project directory.")
@click.option("--debug", is_flag=True, help="Turn on debug logging.")
@click.pass_context
def cli(ctx, profile, region, debug, directory):
    setup_logging(debug)

    # Skip parsing all the things if we just want the versions
    if ctx.invoked_subcommand == "version":
        return

    # Make sure our root is abs
    if directory:
        if not os.path.isabs(directory):
            directory = os.path.normpath(os.path.join(os.getcwd(), directory))
    elif os.getenv("CLOUDBENDER_PROJECT_ROOT"):
        directory = os.getenv("CLOUDBENDER_PROJECT_ROOT")
    else:
        directory = os.getcwd()

    # Read global config
    try:
        cb = CloudBender(directory, profile, region)
    except InvalidProjectDir as e:
        logger.error(e)
        sys.exit(1)

    # Only load stackgroups to get profile and region
    if ctx.invoked_subcommand == "wrap":
        cb.read_config(loadStacks=False)
    else:
        cb.read_config()

    cb.dump_config()

    ctx.obj = cb


@click.command()
def version():
    """Displays own version and all dependencies"""
    logger.error(f"CloudBender: {__version__}")

    # Pulumi
    pulumi_version = get_pulumi_version()
    if not pulumi_version:
        logger.error(
            "Pulumi: Error calling pulumi, see https://www.pulumi.com/docs/get-started/install/"
        )
    else:
        logger.error(f"Pulumi: {pulumi_version}")

    # Docker / podman version
    docker_version = get_docker_version()
    if not docker_version:
        logger.error("Podman/Docker: Cannot call podman nor docker")
    else:
        logger.error(f"Podman/Docker: {docker_version}")


@click.command()
@click.argument("stack_names", nargs=-1)
@click.option("--multi", is_flag=True, help="Allow more than one stack to match")
@click.pass_obj
def render(cb, stack_names, multi):
    """Renders template and its parameters - CFN only"""

    stacks = _find_stacks(cb, stack_names, multi)
    _render(stacks)


@click.command()
@click.argument("stack_names", nargs=-1)
@click.option("--multi", is_flag=True, help="Allow more than one stack to match")
@click.pass_obj
def sync(cb, stack_names, multi):
    """Renders template and provisions it right away"""

    stacks = _find_stacks(cb, stack_names, multi)

    _render(stacks)
    _provision(cb, stacks)


@click.command()
@click.argument("stack_names", nargs=-1)
@click.option("--multi", is_flag=True, help="Allow more than one stack to match")
@click.pass_obj
def validate(cb, stack_names, multi):
    """Validates already rendered templates using cfn-lint - CFN only"""
    stacks = _find_stacks(cb, stack_names, multi)

    for s in stacks:
        ret = s.validate()
        if ret:
            sys.exit(ret)


@click.command()
@click.argument("stack_names", nargs=-1)
@click.option("--multi", is_flag=True, help="Allow more than one stack to match")
@click.option(
    "--include", default=".*", help="regex matching wanted outputs, default '.*'"
)
@click.option(
    "--values",
    is_flag=True,
    help="Only output values, most useful if only one outputs is returned",
)
@click.pass_obj
def outputs(cb, stack_names, multi, include, values):
    """Prints all stack outputs"""

    stacks = _find_stacks(cb, stack_names, multi)
    for s in stacks:
        s.get_outputs()

        for output in s.outputs.keys():
            if re.search(include, output):
                if values:
                    print("{}".format(output))
                else:
                    print("{}={}".format(output, s.outputs[output]))


@click.command()
@click.argument("stack_names", nargs=-1)
@click.option("--multi", is_flag=True, help="Allow more than one stack to match")
@click.pass_obj
def docs(cb, stack_names, multi):
    """Outputs docs for stack(s). For Pulumi stacks prints out docstring. For CloudFormation templates render a markdown file. Same idea as helm-docs."""

    stacks = _find_stacks(cb, stack_names, multi)
    for s in stacks:
        s.docs()


@click.command()
@click.argument("stack_name")
@click.argument("change_set_name")
@click.pass_obj
def create_change_set(cb, stack_name, change_set_name):
    """Creates a change set for an existing stack - CFN only"""
    stacks = _find_stacks(cb, [stack_name])

    for s in stacks:
        s.create_change_set(change_set_name)


@click.command()
@click.argument("stack_name")
@click.pass_obj
def refresh(cb, stack_name):
    """Refreshes Pulumi stack / Drift detection"""
    stacks = _find_stacks(cb, [stack_name])

    for s in stacks:
        if s.mode == "pulumi":
            s.refresh()
        else:
            logger.info("{} uses Cloudformation, refresh skipped.".format(s.stackname))


@click.command()
@click.argument("stack_name")
@click.argument("function", default="")
@click.argument("args", nargs=-1)
@click.pass_obj
def execute(cb, stack_name, function, args):
    """Executes custom Python function within an existing stack context"""
    stacks = _find_stacks(cb, [stack_name])

    for s in stacks:
        if s.mode == "pulumi":
            ret = s.execute(function, args)
            if ret:
                raise click.Abort()
        else:
            logger.info(
                "{} uses Cloudformation, no execute feature available.".format(
                    s.stackname
                )
            )


@click.command()
@click.argument("stack_name")
@click.option(
    "-r",
    "--remove-pending-operations",
    is_flag=True,
    help="All pending stack operations are removed and the stack will be re-imported",
)
@click.pass_obj
def export(cb, stack_name, remove_pending_operations=False):
    """Exports a Pulumi stack to repair state"""
    stacks = _find_stacks(cb, [stack_name])

    for s in stacks:
        if s.mode == "pulumi":
            s.export(remove_pending_operations)
        else:
            logger.info("{} uses Cloudformation, export skipped.".format(s.stackname))


@click.command()
@click.argument("stack_name")
@click.pass_obj
def assimilate(cb, stack_name):
    """Imports potentially existing resources into Pulumi stack"""
    stacks = _find_stacks(cb, [stack_name])

    for s in stacks:
        if s.mode == "pulumi":
            s.assimilate()
        else:
            logger.info(
                "{} uses Cloudformation, cannot assimilate.".format(s.stackname)
            )


@click.command()
@click.argument("stack_name")
@click.argument("key")
@click.argument("value")
@click.option("--secret", is_flag=True, help="Value is a secret")
@click.pass_obj
def set_config(cb, stack_name, key, value, secret=False):
    """Sets a config value, encrypts with stack key if secret"""
    stacks = _find_stacks(cb, [stack_name])

    for s in stacks:
        s.set_config(key, value, secret)


@click.command()
@click.argument("stack_name")
@click.argument("key")
@click.pass_obj
def get_config(cb, stack_name, key):
    """Get a config value, decrypted if secret"""
    stacks = _find_stacks(cb, [stack_name])

    for s in stacks:
        s.get_config(key)


@click.command()
@click.argument("stack_name")
@click.pass_obj
def preview(cb, stack_name):
    """Preview of Pulumi stack up operation"""
    stacks = _find_stacks(cb, [stack_name])

    for s in stacks:
        if s.mode == "pulumi":
            s.preview()
        else:
            logger.warning(
                "{} uses Cloudformation, use create-change-set for previews.".format(
                    s.stackname
                )
            )


@click.command()
@click.argument("stack_names", nargs=-1)
@click.option("--multi", is_flag=True, help="Allow more than one stack to match")
@click.pass_obj
def provision(cb, stack_names, multi):
    """Creates or updates stacks or stack groups"""

    stacks = _find_stacks(cb, stack_names, multi)
    _provision(cb, stacks)


@click.command()
@click.argument("stack_names", nargs=-1)
@click.option("--multi", is_flag=True, help="Allow more than one stack to match")
@click.pass_obj
def delete(cb, stack_names, multi):
    """Deletes stacks or stack groups"""
    stacks = _find_stacks(cb, stack_names, multi)

    # Reverse steps
    steps = [s for s in sort_stacks(cb, stacks)]
    delete_steps = steps[::-1]
    for step in delete_steps:
        if step:
            with ThreadPoolExecutor(max_workers=len(step)) as group:
                futures = []
                for stack in step:
                    if stack.multi_delete:
                        futures.append(group.submit(stack.delete))

                for future in as_completed(futures):
                    future.result()


@click.command()
@click.argument("stack_group", nargs=1, required=True)
@click.argument("cmd", nargs=-1, required=True)
@click.pass_obj
def wrap(cb, stack_group, cmd):
    """Execute custom external program"""

    sg = cb.sg.get_stackgroup(stack_group)
    cb.wrap(sg, " ".join(cmd))


@click.command()
@click.pass_obj
def clean(cb):
    """Deletes all previously rendered files locally"""
    cb.clean()


def sort_stacks(cb, stacks):
    """Sort stacks by dependencies"""

    data = {}
    for s in stacks:
        if s.mode == "pulumi":
            data[s.id] = set()
            continue

        # To resolve dependencies we have to read each template
        s.read_template_file()
        deps = []
        for d in s.dependencies:
            # For now we assume deps are artifacts so we prepend them with our local profile and region to match stack.id
            for dep_stack in cb.filter_stacks(
                {"region": s.region, "profile": s.profile, "provides": d}
            ):
                deps.append(dep_stack.id)
            # also look for global services
            for dep_stack in cb.filter_stacks(
                {"region": "global", "profile": s.profile, "provides": d}
            ):
                deps.append(dep_stack.id)

        data[s.id] = set(deps)
        logger.debug("Stack {} depends on {}".format(s.id, deps))

    # Ignore self dependencies
    for k, v in data.items():
        v.discard(k)

    if data:
        extra_items_in_deps = functools.reduce(set.union, data.values()) - set(
            data.keys()
        )
        data.update({item: set() for item in extra_items_in_deps})

    while True:
        ordered = set(item for item, dep in data.items() if not dep)
        if not ordered:
            break

        # return list of stack objects rather than just names
        result = []
        for o in ordered:
            for s in stacks:
                if s.id == o:
                    result.append(s)
        yield result

        data = {
            item: (dep - ordered) for item, dep in data.items() if item not in ordered
        }
    assert not data, "A cyclic dependency exists amongst %r" % data


def _find_stacks(cb, stack_names, multi=False):
    """search stacks by name"""

    stacks = []
    for s in stack_names:
        stacks = stacks + cb.resolve_stacks(s)

    if not multi and len(stacks) > 1:
        logger.error(
            "Found more than one stack matching name ({}). Please set --multi if that is what you want.".format(
                ", ".join(stack_names)
            )
        )
        raise click.Abort()

    if not stacks:
        logger.error("Cannot find stack matching: {}".format(", ".join(stack_names)))
        raise click.Abort()

    return stacks


def _render(stacks):
    """Utility function to reuse code between tasks"""
    for s in stacks:
        if s.mode != "pulumi":
            s.render()
            s.write_template_file()
        else:
            logger.info("{} uses Pulumi, render skipped.".format(s.stackname))


def _anyPulumi(step):
    for stack in step:
        if stack.mode == "pulumi":
            return True

    return False


def _provision(cb, stacks):
    """Utility function to reuse code between tasks"""
    for step in sort_stacks(cb, stacks):
        if step:
            # Pulumi is not thread safe, so for now one by one
            if _anyPulumi(step) and False:
                for stack in step:
                    if stack.mode != "pulumi":
                        status = stack.get_status()
                        if not status:
                            stack.create()
                        else:
                            stack.update()

                    # Pulumi only needs "up"
                    else:
                        stack.create()

            else:
                with ThreadPoolExecutor(max_workers=len(step)) as group:
                    futures = []
                    for stack in step:
                        if stack.mode != "pulumi":
                            status = stack.get_status()
                            if not status:
                                futures.append(group.submit(stack.create))
                            else:
                                futures.append(group.submit(stack.update))

                        # Pulumi only needs "up"
                        else:
                            futures.append(group.submit(stack.create))

                    for future in as_completed(futures):
                        future.result()


cli.add_command(version)
cli.add_command(render)
cli.add_command(sync)
cli.add_command(validate)
cli.add_command(provision)
cli.add_command(delete)
cli.add_command(clean)
cli.add_command(create_change_set)
cli.add_command(outputs)
cli.add_command(docs)
cli.add_command(refresh)
cli.add_command(preview)
cli.add_command(set_config)
cli.add_command(get_config)
cli.add_command(export)
cli.add_command(assimilate)
cli.add_command(execute)
cli.add_command(wrap)

if __name__ == "__main__":
    cli(obj={})
