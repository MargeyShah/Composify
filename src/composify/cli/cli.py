import click
from pathlib import Path
from typing import List, Optional, Tuple
from composify import Service, append_to_include_with_comment, dump_include_only_str, dump_yaml_str, list_yaml_files, load_main_compose, simulate_include_after_append_str, upsert_service_in_file, write_new_stack_file, list_middleware_chains


DEFAULT_ROOT = Path("/home/margey/docker")
DEFAULT_STACKS_DIR = DEFAULT_ROOT / "stacks"
MAIN_COMPOSE = DEFAULT_ROOT / "docker-compose.yml"
MIDDLEWARE_CHAINS_FILE = Path("/home/margey/docker/apps/traefik2/rules/middleware-chains.yml")


def _choose_from_list(title: str, items: List[str]) -> Optional[str]:
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
    raw = click.prompt("Profiles (comma-separated, optional)", default="", show_default=False).strip()
    return [raw] if raw else []


def _prompt_middleware_chain() -> Optional[str]:
    options = list_middleware_chains(MIDDLEWARE_CHAINS_FILE)
    if not options:
        # Fallback: let user type a chain name, or skip
        val = click.prompt(
            "Middleware chain (no options file found; enter a name or leave blank to skip)",
            default="",
            show_default=False,
        ).strip()
        return val or None
    choice = _choose_from_list("Select a middleware chain for Traefik:", options)
    return choice  # None if canceled


def _prompt_service_details(default_name: Optional[str] = None) -> Service:
    name = click.prompt("Service/container name", default=default_name or "", show_default=bool(default_name)).strip()
    image = click.prompt("Docker image URL (e.g., ghcr.io/org/app:tag)").strip()
    container_path = click.prompt("Internal container volume path (e.g., /config)").strip()
    profiles = _prompt_profiles()
    expose = click.confirm("Expose via Traefik (adds networks: t2_proxy and labels)?", default=False)

    # Always prompt internal port; used for Traefik load balancer or ports mapping
    internal_port = click.prompt("Container internal port", type=int, default=8084)

    middleware_chain: Optional[str] = None
    external_port: Optional[int] = None
    if expose:
        # Ask which chain to use for the middlewares label
        middleware_chain = _prompt_middleware_chain()
    else:
        # If not exposing via Traefik, ask for LAN port (defaults to internal)
        external_port = click.prompt("LAN port to expose", type=int, default=internal_port)

    return Service(
        name=name,
        image=image,
        container_path=container_path,
        profiles=profiles,
        expose=expose,
        internal_port=internal_port,
        external_port=external_port,
        middleware_chain=middleware_chain,
    )


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
        files = list_yaml_files(DEFAULT_ROOT)
        choices = [str(p.relative_to(DEFAULT_ROOT)) for p in files]
        choice = _choose_from_list(f"Select a compose file under {DEFAULT_ROOT}", choices)
        if not choice:
            click.secho("Canceled.", fg="yellow")
            return
        compose_path = DEFAULT_ROOT / choice

        svc = _prompt_service_details()
        snippet = {svc.name: svc.to_compose_value()}
        click.echo("\nThis service will be appended to the selected compose file:")
        click.echo(click.style(dump_yaml_str(snippet), fg="white"))

        if not click.confirm(f"Proceed to append to {compose_path}?", default=True):
            click.secho("No changes made.", fg="yellow")
            return

        overwrite = click.confirm("Overwrite if the service exists?", default=False)
        try:
            upsert_service_in_file(compose_path, svc, overwrite=overwrite)
        except SystemExit as e:
            raise click.ClickException(str(e))

        click.secho(f"Service '{svc.name}' written to {compose_path}", fg="green")

    else:  # action == "new"
        folder = click.prompt(f"New stack folder name (under {DEFAULT_STACKS_DIR})").strip()
        stack_dir = DEFAULT_STACKS_DIR / folder
        stack_compose = stack_dir / "docker-compose.yml"
        rel_include_path = str(stack_compose.relative_to(DEFAULT_ROOT)).replace("\\", "/")

        svc = _prompt_service_details(default_name=folder)

        new_stack_obj = {"services": {svc.name: svc.to_compose_value()}}
        click.echo("\nNew stack file will be created with content:")
        click.echo(click.style(dump_yaml_str(new_stack_obj), fg="white"))

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

        try:
            write_new_stack_file(
                stack_compose,
                svc,
                overwrite=click.confirm(f"{stack_compose} exists. Overwrite?", default=False),
            )
            appended = append_to_include_with_comment(MAIN_COMPOSE, rel_include_path, comment)
        except SystemExit as e:
            raise click.ClickException(str(e))

        click.secho(f"Created stack: {stack_compose}", fg="green")
        if appended:
            click.secho(f"Updated include in {MAIN_COMPOSE}", fg="green")
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
