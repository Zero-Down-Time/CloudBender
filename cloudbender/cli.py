import os
import sys
import click
import functools

from concurrent.futures import ThreadPoolExecutor, as_completed

from . import __version__
from .core import CloudBender
from .utils import setup_logging
from .exceptions import InvalidProjectDir

import logging
logger = logging.getLogger(__name__)


@click.group()
@click.version_option(version=__version__, prog_name="CloudBender")
@click.option("--debug", is_flag=True, help="Turn on debug logging.")
@click.option("--dir", "directory", help="Specify cloudbender project directory.")
@click.pass_context
def cli(ctx, debug, directory):
    setup_logging(debug)

    # Make sure our root is abs
    if directory:
        if not os.path.isabs(directory):
            directory = os.path.normpath(os.path.join(os.getcwd(), directory))
    elif os.getenv('CLOUDBENDER_PROJECT_ROOT'):
        directory = os.getenv('CLOUDBENDER_PROJECT_ROOT')
    else:
        directory = os.getcwd()

    # Read global config
    try:
        cb = CloudBender(directory)
    except InvalidProjectDir as e:
        print(e)
        sys.exit(1)

    cb.read_config()
    cb.dump_config()

    ctx.obj = cb


@click.command()
@click.argument("stack_names", nargs=-1)
@click.option("--multi", is_flag=True, help="Allow more than one stack to match")
@click.pass_obj
def render(cb, stack_names, multi):
    """ Renders template and its parameters """

    stacks = _find_stacks(cb, stack_names, multi)
    _render(stacks)


@click.command()
@click.argument("stack_names", nargs=-1)
@click.option("--multi", is_flag=True, help="Allow more than one stack to match")
@click.pass_obj
def sync(cb, stack_names, multi):
    """ Renders template and provisions it right away """

    stacks = _find_stacks(cb, stack_names, multi)

    _render(stacks)
    _provision(cb, stacks)


@click.command()
@click.argument("stack_names", nargs=-1)
@click.option("--multi", is_flag=True, help="Allow more than one stack to match")
@click.pass_obj
def validate(cb, stack_names, multi):
    """ Validates already rendered templates using cfn-lint """
    stacks = _find_stacks(cb, stack_names, multi)

    for s in stacks:
        s.validate()


@click.command()
@click.argument("stack_names", nargs=-1)
@click.option("--multi", is_flag=True, help="Allow more than one stack to match")
@click.option("--include", default='.*', help="regex matching wanted outputs, default '.*'")
@click.option("--values", is_flag=True, help="Only output values, most useful if only one outputs is returned")
@click.pass_obj
def outputs(cb, stack_names, multi, include, values):
    """ Prints all stack outputs """

    stacks = _find_stacks(cb, stack_names, multi)
    for s in stacks:
        s.get_outputs(include, values)


@click.command()
@click.argument("stack_names", nargs=-1)
@click.option("--multi", is_flag=True, help="Allow more than one stack to match")
@click.pass_obj
def create_docs(cb, stack_names, multi):
    """ Parses all documentation fragments out of rendered templates creating docs/*.md file """

    stacks = _find_stacks(cb, stack_names, multi)
    for s in stacks:
        s.create_docs()


@click.command()
@click.argument("stack_name")
@click.argument("change_set_name")
@click.pass_obj
def create_change_set(cb, stack_name, change_set_name):
    """ Creates a change set for an existing stack """
    stacks = _find_stacks(cb, [stack_name])

    for s in stacks:
        s.create_change_set(change_set_name)


@click.command()
@click.argument("stack_names", nargs=-1)
@click.option("--multi", is_flag=True, help="Allow more than one stack to match")
@click.pass_obj
def provision(cb, stack_names, multi):
    """ Creates or updates stacks or stack groups """

    stacks = _find_stacks(cb, stack_names, multi)
    _provision(cb, stacks)


@click.command()
@click.argument("stack_names", nargs=-1)
@click.option("--multi", is_flag=True, help="Allow more than one stack to match")
@click.pass_obj
def delete(cb, stack_names, multi):
    """ Deletes stacks or stack groups """
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
@click.pass_obj
def clean(cb):
    """ Deletes all previously rendered files locally """
    cb.clean()


def sort_stacks(cb, stacks):
    """ Sort stacks by dependencies """

    data = {}
    for s in stacks:
        # To resolve dependencies we have to read each template
        s.read_template_file()
        deps = []
        for d in s.dependencies:
            # For now we assume deps are artifacts so we prepend them with our local profile and region to match stack.id
            for dep_stack in cb.filter_stacks({'region': s.region, 'profile': s.profile, 'provides': d}):
                deps.append(dep_stack.id)
            # also look for global services
            for dep_stack in cb.filter_stacks({'region': 'global', 'profile': s.profile, 'provides': d}):
                deps.append(dep_stack.id)

        data[s.id] = set(deps)
        logger.debug("Stack {} depends on {}".format(s.id, deps))

    # Ignore self dependencies
    for k, v in data.items():
        v.discard(k)

    extra_items_in_deps = functools.reduce(set.union, data.values()) - set(data.keys())
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

        data = {item: (dep - ordered) for item, dep in data.items()
                if item not in ordered}
    assert not data, "A cyclic dependency exists amongst %r" % data


def _find_stacks(cb, stack_names, multi=False):
    """ search stacks by name """

    stacks = []
    for s in stack_names:
        stacks = stacks + cb.resolve_stacks(s)

    if not multi and len(stacks) > 1:
        logger.error('Found more than one stack matching name ({}). Please set --multi if that is what you want.'.format(', '.join(stack_names)))
        raise click.Abort()

    if not stacks:
        logger.error('Cannot find stack matching: {}'.format(', '.join(stack_names)))
        raise click.Abort()

    return stacks


def _render(stacks):
    """ Utility function to reuse code between tasks """
    for s in stacks:
        s.render()
        s.write_template_file()


def _provision(cb, stacks):
    """ Utility function to reuse code between tasks """
    for step in sort_stacks(cb, stacks):
        if step:
            with ThreadPoolExecutor(max_workers=len(step)) as group:
                futures = []
                for stack in step:
                    status = stack.get_status()
                    if not status:
                        futures.append(group.submit(stack.create))
                    else:
                        futures.append(group.submit(stack.update))

                for future in as_completed(futures):
                    future.result()


cli.add_command(render)
cli.add_command(sync)
cli.add_command(validate)
cli.add_command(provision)
cli.add_command(delete)
cli.add_command(clean)
cli.add_command(create_change_set)
cli.add_command(outputs)
cli.add_command(create_docs)

if __name__ == '__main__':
    cli(obj={})
