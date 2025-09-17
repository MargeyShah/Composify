import click
from pathlib import Path
from typing import List, Optional, Tuple
from composify import ComposeStack, Service, append_to_include_with_comment, dump_include_only_str, dump_yaml_str, list_yaml_files, load_main_compose, load_stack, simulate_include_after_append_str, write_stack

DEFAULT_ROOT = Path("/home/margey/docker")
DEFAULT_STACKS_DIR = DEFAULT_ROOT / "stacks"
MAIN_COMPOSE = DEFAULT_ROOT / "docker-compose.yml"



def _choose_from_list(title: str, items: List[str]) -> Optional[str]:
    """Simple numeric chooser; returns selected item or None if aborted."""
    if not items:
        click.secho("No items to choose from.", fg="yellow")
        return None
    click.echo(title)
    for i, it in enumerate(items, start=1):
        click.echo(f"  {i}. {it}")
    idx = click.prompt("Enter number (or 0 to cancel)", type=int, default=0)
    if idx <= 0 or idx > len(items):
        return None
    return items[idx - 1]


def _prompt_profiles() -> List[str]:
    raw = click.prompt(
        "Profiles (comma-separated, optional)",
        default="",
        show_default=False,
    ).strip()
    return [raw] if raw else []


def _prompt_service_details(default_name: Optional[str] = None) -> Tuple[str, str, str, List[str], bool, int]:
    """
    Prompts for: service name, image, container_path, profiles, expose, and picks a service_port automatically.
    - If expose=True: choose default internal service port 8084 (no prompt).
    - If expose=False: port is irrelevant (no ports mapping is created); we still carry a default internally.
    """
    name = click.prompt("Service/container name", default=default_name or "", show_default=bool(default_name)).strip()
    image = click.prompt("Docker image URL (e.g., ghcr.io/org/app:tag)").strip()
    container_path = click.prompt("Internal container volume path (e.g., /config)").strip()
    profiles = _prompt_profiles()
    expose = click.confirm("Expose via Traefik (adds networks: t2_proxy and labels)?", default=False)
    service_port = 8084 if expose else 8084  # same default; only used when expose=True
    return name, image, container_path, profiles, expose, service_port


@click.command()
def cli():
    """Interactive Compose helper."""
    click.secho("Welcome to the Compose helper", fg="cyan")

    action = click.prompt(
        "What would you like to do?",
        type=click.Choice(["append", "new"], case_sensitive=False),
        default="append",
        show_choices=True,
        show_default=True,
    ).lower()

    if action == "append":
        # 1) Choose existing compose file (list all *.yml under DEFAULT_ROOT)
        files = list_yaml_files(DEFAULT_ROOT)
        choices = [str(p.relative_to(DEFAULT_ROOT)) for p in files]
        choice = _choose_from_list(f"Select a compose file under {DEFAULT_ROOT}", choices)
        if not choice:
            click.secho("Canceled.", fg="yellow")
            return
        compose_path = DEFAULT_ROOT / choice

        # 2) Validate target has services:
        try:
            _ = load_stack(compose_path)
        except SystemExit as e:
            raise click.ClickException(str(e))

        # 3) Prompt for service details
        name, image, container_path, profiles, expose, service_port = _prompt_service_details()

        # 4) Build service and preview YAML snippet
        svc = Service(
            name=name,
            image=image,
            container_path=container_path,
            profiles=profiles,
            expose=expose,
            service_port=service_port,
        )
        snippet = {svc.name: svc.to_compose_value()}
        click.echo("\nThis service will be appended to the selected compose file:")
        click.echo(click.style(dump_yaml_str(snippet), fg="white"))

        if not click.confirm(f"Proceed to append to {compose_path}?", default=True):
            click.secho("No changes made.", fg="yellow")
            return

        # 5) Write
        try:
            stack = load_stack(compose_path)
            stack.add_or_replace(svc, overwrite=True if click.confirm("Overwrite if service exists?", default=False) else False)
            write_stack(compose_path, stack)
        except SystemExit as e:
            raise click.ClickException(str(e))

        click.secho(f"Service '{svc.name}' appended to {compose_path}", fg="green")

    else:  # action == "new"
        # 1) Ask for new folder name (under DEFAULT_STACKS_DIR)
        folder = click.prompt(f"New stack folder name (under {DEFAULT_STACKS_DIR})").strip()
        stack_dir = DEFAULT_STACKS_DIR / folder
        stack_compose = stack_dir / "docker-compose.yml"
        rel_include_path = str(stack_compose.relative_to(DEFAULT_ROOT)).replace("\\", "/")

        # 2) Prompt for service details (service name can be different from folder)
        name, image, container_path, profiles, expose, service_port = _prompt_service_details(default_name=folder)

        svc = Service(
            name=name,
            image=image,
            container_path=container_path,
            profiles=profiles,
            expose=expose,
            service_port=service_port,
        )
        stack = ComposeStack.new_with_service(svc)

        # 3) Preview the new stack YAML
        click.echo("\nNew stack file will be created with content:")
        click.echo(click.style(dump_yaml_str(stack.to_compose_dict()), fg="white"))

        # 4) Preview include block change on MAIN_COMPOSE
        comment = svc.primary_profile_title()
        click.echo(f"\nMain compose: {MAIN_COMPOSE}")
        click.echo("The following include entry will be added (with comment):")
        click.echo(f"- Path: {rel_include_path}")
        click.echo(f"- Comment: # {comment}")
        include_after = simulate_include_after_append_str(MAIN_COMPOSE, rel_include_path, comment)
        click.echo("\ninclude: (after append)")
        click.echo(click.style(include_after if include_after.strip() else "include: []", fg="white"))

        if not click.confirm("Proceed to create the new stack and update main include?", default=True):
            click.secho("No changes made.", fg="yellow")
            return

        # 5) Write stack file, then update include in main compose
        if stack_compose.exists() and not click.confirm(f"{stack_compose} exists. Overwrite?", default=False):
            click.secho("Aborting to avoid overwrite.", fg="red")
            return

        write_stack(stack_compose, stack)
        appended = append_to_include_with_comment(MAIN_COMPOSE, rel_include_path, comment)

        click.secho(f"Created stack: {stack_compose}", fg="green")
        if appended:
            click.secho(f"Updated include in {MAIN_COMPOSE}", fg="green")
            # Show the final include block
            final_include = dump_include_only_str(load_main_compose(MAIN_COMPOSE))
            click.echo("\ninclude: (current)")
            click.echo(click.style(final_include if final_include.strip() else "include: []", fg="white"))
        else:
            click.secho("Include entry already present; no changes to include list.", fg="yellow")

# @click.group()
# def cli():
#     click.echo('Welcome')
#
# @cli.command()
# @click.option('--path', type=click.File('w'), required=True) 
# @click.option('--name', type=str, help='Name of the service/container')
# def append(path: click.File, name: str):
#     """Simple program that greets NAME for a total of COUNT times."""
#     click.echo(f"Hello {path}, name: {name}!")
#
# @cli.command()
# @click.option('--path', type=click.Path(), required=False) 
# @click.option('--name', type=str, help='Name of the service/container')
# def new(path: click.Path, name: str):
#     """Simple program that greets NAME for a total of COUNT times."""
#     click.echo(f"Hello {path}, name: {name}!")
#
#
# cli = click.CommandCollection(sources=[cli])
# if __name__ == '__main__':
#     cli()
if __name__ == "__main__":
    cli()
