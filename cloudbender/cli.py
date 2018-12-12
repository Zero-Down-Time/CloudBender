import os
import click
import functools

from concurrent.futures import ThreadPoolExecutor, as_completed

from . import __version__
from .core import CloudBender
from .utils import setup_logging

import logging
logger = logging.getLogger(__name__)

@click.group()
@click.version_option(version=__version__, prog_name="CloudBender")
@click.option("--debug", is_flag=True, help="Turn on debug logging.")
@click.option("--dir", "directory", help="Specify cloudbender project directory.")
@click.pass_context
def cli(ctx, debug, directory):
    logger = setup_logging(debug)

    # Read global config
    cb = CloudBender(directory if directory else os.getcwd())
    cb.read_config()
    cb.dump_config()

    ctx.obj['cb'] = cb


@click.command()
@click.argument("stack_name")
@click.option("--multi", is_flag=True, help="Allow more than one stack to match")
@click.pass_context
def render(ctx, stack_name, multi):
    """ Renders template and its parameters """

    stacks = _find_stacks(ctx, stack_name, multi)

    for s in stacks:
        s.render()
        s.write_template_file()


@click.command()
@click.argument("stack_name")
@click.option("--multi", is_flag=True, help="Allow more than one stack to match")
@click.pass_context
def validate(ctx, stack_name, multi):
    stacks = _find_stacks(ctx, stack_name, multi)

    for s in stacks:
        s.validate()


@click.command()
@click.argument("stack_name")
@click.option("--multi", is_flag=True, help="Allow more than one stack to match")
@click.pass_context
def provision(ctx, stack_name, multi):
    """ Creates or updates stacks or stack groups """
    stacks = _find_stacks(ctx, stack_name, multi)

    for step in sort_stacks(ctx, stacks):
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
                    result = future.result()


@click.command()
@click.argument("stack_name")
@click.option("--multi", is_flag=True, help="Allow more than one stack to match")
@click.pass_context
def delete(ctx, stack_name, multi):
    """ Deletes stacks or stack groups """
    stacks = _find_stacks(ctx, stack_name, multi)

    # Reverse steps
    steps = [s for s in sort_stacks(ctx, stacks)]
    delete_steps = steps[::-1]
    for step in delete_steps:
        if step:
            with ThreadPoolExecutor(max_workers=len(step)) as group:
                futures = []
                for stack in step:
                    if stack.multi_delete:
                        futures.append(group.submit(stack.delete))

                for future in as_completed(futures):
                    result = future.result()


@click.command()
@click.pass_context
def clean(ctx):
    """ Deletes all previously rendered files locally """
    cb = ctx.obj['cb']
    cb.clean()


def sort_stacks(ctx, stacks):
    """ Sort stacks by dependencies """
    cb = ctx.obj['cb']

    data = {}
    for s in stacks:
        # Resolve dependencies
        deps = []
        for d in s.dependencies:
            # For now we assume deps are artifacts so we prepend them with our local profile and region to match stack.id
            for dep_stack in cb.filter_stacks({'region': s.region, 'profile': s.profile, 'provides': d}):
                deps.append(dep_stack.id)

        data[s.id] = set(deps)

    for k, v in data.items():
        v.discard(k) # Ignore self dependencies

    extra_items_in_deps = functools.reduce(set.union, data.values()) - set(data.keys())
    data.update({item:set() for item in extra_items_in_deps})
    while True:
        ordered = set(item for item,dep in data.items() if not dep)
        if not ordered:
            break

        # return list of stack objects rather than just names
        result = []
        for o in ordered:
            for s in stacks:
                if s.id == o: result.append(s)
        yield result

        data = {item: (dep - ordered) for item,dep in data.items()
                if item not in ordered}
    assert not data, "A cyclic dependency exists amongst %r" % data


def _find_stacks(ctx, stack_name,multi):
    cb = ctx.obj['cb']

    # ALL acts ass config and multi=True
    if stack_name == "ALL":
        multi = True
        stack_name = "config"

    stacks = cb.resolve_stacks(stack_name)

    if not stacks:
        logger.error('Cannot find stack matching: {}'.format(stack_name))
        raise click.Abort()

    if not multi and len(stacks) > 1:
        logger.error('Found more than one ({}) stacks matching name {}: {}. Abort.'.format(len(stacks), stack_name, [s.stackname for s in stacks]))
        raise click.Abort()

    return stacks


cli.add_command(render)
cli.add_command(validate)
cli.add_command(provision)
cli.add_command(delete)
cli.add_command(clean)

if __name__ == '__main__':
    cli(obj={})
