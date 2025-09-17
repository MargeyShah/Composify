import click
from pathlib import Path
from typing import List, Optional

from composify import (
    Service,
    append_to_include_with_comment,
    dump_include_only_str,
    dump_yaml_str,
    list_yaml_files,
    load_main_compose,
    upsert_service_in_file,
    write_new_stack_file,
    list_middleware_chains,
    get_existing_service_names,
)

DEFAULT_ROOT = Path("/home/margey/docker")
DEFAULT_STACKS_DIR = DEFAULT_ROOT / "stacks"
MAIN_COMPOSE = DEFAULT_ROOT / "docker-compose.yml"
MIDDLEWARE_CHAINS_FILE = Path("/home/margey/docker/apps/traefik2/rules/middleware-chains.yml")

RESTART_CHOICES = ["always", "unless-stopped", "on-failure", "no"]


class CommaList(click.ParamType):
    name = "comma-list"

    def convert(self, value, param, ctx):
        if value is None:
            return []
        if isinstance(value, list):
            return value
        s = str(value).strip()
        if not s:
            return []
        return [p.strip() for p in s.split(",") if p.strip()]


def choose_from_list(title: str, items: List[str], default_index: int | None = None) -> Optional[str]:
    if not items:
        click.secho("No items to choose from.", fg="yellow")
        return None
    while True:
        click.echo(title)
        for i, it in enumerate(items, start=1):
            click.echo(f"  {i}. {it}")
        prompt_kwargs = {"type": int}
        if default_index is not None:
            prompt_kwargs["default"] = default_index
            click.echo(f"Press Enter for default [{default_index}]")
        idx = click.prompt("Enter number (0 to cancel)", **prompt_kwargs)
        if idx == 0:
            return None
        if 1 <= idx <= len(items):
            return items[idx - 1]
        click.secho("Invalid selection. Try again.", fg="red")


def choose_compose_file(base_dir: Path = DEFAULT_STACKS_DIR) -> Optional[Path]:
    files = list_yaml_files(base_dir)
    choices = [str(p.relative_to(base_dir)) for p in files]
    choice = choose_from_list(f"Select a compose file under {base_dir}", choices, default_index=None)
    if not choice:
        return None
    return base_dir / choice


# Callbacks for conditional/dynamic prompts

def choose_compose_cb(ctx: click.Context, param: click.Option, value: Optional[Path]) -> Path:
    if value is not None:
        # Enforce it's under stacks
        try:
            value.relative_to(DEFAULT_STACKS_DIR)
        except ValueError:
            raise click.BadParameter(f"{value} is not under {DEFAULT_STACKS_DIR}")
        return value
    path = choose_compose_file(DEFAULT_STACKS_DIR)
    if path is None:
        raise click.Abort()
    return path


def unique_name_cb(ctx: click.Context, param: click.Option, value: Optional[str]) -> str:
    if not value:
        value = click.prompt("Service/container name").strip()
    compose_path: Path | None = ctx.params.get("compose_path")
    if compose_path:
        try:
            existing = set(get_existing_service_names(compose_path))
        except SystemExit as e:
            raise click.ClickException(str(e))
        if value in existing:
            raise click.BadParameter(
                click.style(f"A service named '{value}' already exists in {compose_path}.", fg='red')
            )
    return value


def name_with_folder_default_cb(ctx: click.Context, param: click.Option, value: Optional[str]) -> str:
    if value:
        return value.strip()
    folder = ctx.params.get("folder", "") or ""
    return click.prompt(
        "Service/container name",
        default=folder,
        show_default=bool(folder),
    ).strip()


def external_port_cb(ctx: click.Context, param: click.Option, value: Optional[int]) -> Optional[int]:
    expose: bool = ctx.params.get("expose", False)
    internal_port: int | None = ctx.params.get("internal_port")
    if expose:
        if value is not None:
            click.echo("Ignoring --external-port because --expose was set.", err=True)
        return None
    if value is None:
        value = click.prompt("LAN port to expose", type=int, default=internal_port)
    return value


def middleware_chain_cb(ctx: click.Context, param: click.Option, value: Optional[str]) -> Optional[str]:
    expose: bool = ctx.params.get("expose", False)
    if not expose:
        return None
    if value:
        return value
    options = list_middleware_chains(MIDDLEWARE_CHAINS_FILE)
    if options:
        choice = choose_from_list("Select a middleware chain for Traefik:", options)
        return choice  # may be None to skip
    raw = click.prompt(
        "Middleware chain (file not found or empty; enter a name or leave blank to skip)",
        default="",
        show_default=False,
    ).strip()
    return raw or None


def restart_select_cb(ctx: click.Context, param: click.Option, value: Optional[str]) -> str:
    # Numbered selection; default to "unless-stopped"
    if value:
        v = value.strip().lower()
        if v in RESTART_CHOICES:
            return v
        raise click.BadParameter(f"Must be one of: {', '.join(RESTART_CHOICES)}")
    choice = choose_from_list("Select restart policy:", RESTART_CHOICES, default_index=2)
    return choice or "unless-stopped"


def folder_available_cb(ctx: click.Context, param: click.Option, value: Optional[str]) -> str:
    # Ensure the chosen folder under DEFAULT_STACKS_DIR does not already exist; reprompt if it does.
    prompt_text = f"New stack folder name (under {DEFAULT_STACKS_DIR})"
    v = (value or "").strip()
    while True:
        if not v:
            v = click.prompt(prompt_text).strip()
        stack_dir = DEFAULT_STACKS_DIR / v
        if stack_dir.exists():
            click.secho(f"{stack_dir} already exists. Please choose a different folder name.", fg="red")
            v = ""  # trigger another prompt
            continue
        return v


@click.group()
def cli():
    """Compose helper."""


# NEW command
@cli.command()
# Bottom-most decorators prompt first
@click.option(
    "--folder",
    prompt=f"New stack folder name (under {DEFAULT_STACKS_DIR})",
    callback=folder_available_cb,
    help="Folder name under stacks/",
)
@click.option("--name", prompt=False, callback=name_with_folder_default_cb, help="Service/container name")
@click.option("--image", prompt="Docker image URL (e.g., ghcr.io/org/app:tag)", help="Image ref")
@click.option("--container-path", prompt="Internal container volume path (e.g., /config)", help="Path inside container")
# Traefik question should appear right above Profiles
@click.option("--expose/--no-expose", default=False, prompt="Expose via Traefik (adds networks: t2_proxy and labels)?", help="Expose through Traefik")
@click.option("--internal-port", type=int, prompt="Container internal port", help="Container internal port")
@click.option("--external-port", type=int, default=None, callback=external_port_cb, help="LAN port (when not exposing)")
@click.option("--profiles", type=CommaList(), default="", prompt="Profiles (comma-separated, optional)", show_default=False, help="Compose profiles")
@click.option("--restart", default=None, callback=restart_select_cb, help="Container restart policy")
@click.option("--middleware-chain", default=None, callback=middleware_chain_cb, help="Traefik middleware chain")
def new(
    folder: str,
    name: str,
    image: str,
    container_path: str,
    expose: bool,
    internal_port: int,
    external_port: Optional[int],
    profiles: List[str],
    restart: Optional[str],
    middleware_chain: Optional[str],
):
    """Create a new stack and include it in main compose."""
    stack_dir = DEFAULT_STACKS_DIR / folder
    stack_compose = stack_dir / "docker-compose.yml"
    rel_include_path = str(stack_compose.relative_to(DEFAULT_ROOT)).replace("\\", "/")

    svc = Service(
        name=name,
        image=image,
        container_path=container_path,
        profiles=profiles,
        restart=restart or "unless-stopped",
        expose=expose,
        internal_port=internal_port,
        external_port=external_port,
        middleware_chain=middleware_chain,
        # environment defaults are applied in the Service model (PUID/PGID/TZ)
    )

    # Preview of new stack content (only YAML is green)
    new_stack_obj = {"services": {svc.name: svc.to_compose_value()}}
    click.echo("\nNew stack file will be created with content:")
    click.secho(dump_yaml_str(new_stack_obj), fg="green")

    comment = svc.primary_profile_title()
    click.echo(f"\nMain compose: {MAIN_COMPOSE}")
    click.echo("The following include entry will be added (with comment):")
    click.echo(f"- Path: {rel_include_path}")
    click.echo(f"- Comment: # {comment}")

    # Show include: current (not colored). If main compose doesn't exist, just show what will be appended.
    if MAIN_COMPOSE.exists():
        click.echo("\ninclude: (current)")
        try:
            current_include = dump_include_only_str(load_main_compose(MAIN_COMPOSE)).rstrip()
            click.echo(current_include if current_include.strip() else "include: []")
        except SystemExit as e:
            raise click.ClickException(str(e))
        click.echo("\ninclude: (to append)")
        to_append = f"# {comment}\n- {rel_include_path}"
        click.secho(to_append, fg="green")
    else:
        click.echo("\ninclude: (to append)")
        to_append = f"# {comment}\n- {rel_include_path}"
        click.secho(to_append, fg="green")

    if not click.confirm("Proceed to create the new stack and update main include?", default=True):
        click.secho("No changes made.", fg="yellow")
        return

    try:
        overwrite_file = False
        if stack_compose.exists():
            overwrite_file = click.confirm(f"{stack_compose} exists. Overwrite?", default=False)
        write_new_stack_file(stack_compose, svc, overwrite=overwrite_file)
        appended = append_to_include_with_comment(MAIN_COMPOSE, rel_include_path, comment)
    except SystemExit as e:
        raise click.ClickException(str(e))

    click.secho(f"Created stack: {stack_compose}", fg="green")
    if appended:
        click.secho(f"Updated include in {MAIN_COMPOSE}", fg="green")
        final_include = dump_include_only_str(load_main_compose(MAIN_COMPOSE))
        click.echo("\ninclude: (current)")
        click.echo(final_include if final_include.strip() else "include: []")
    else:
        click.secho("Include entry already present; no changes to include list.", fg="yellow")


# APPEND command
@cli.command()
# Bottom-most first: compose path selection
@click.option(
    "--compose",
    "compose_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    callback=choose_compose_cb,  # selects from DEFAULT_STACKS_DIR if omitted
    help=f"Compose YAML file (under {DEFAULT_STACKS_DIR}). If omitted, you'll choose from a list.",
)
@click.option("--name", prompt="Service/container name", callback=unique_name_cb, help="Service/container name (must be unique in selected compose)")
@click.option("--image", prompt="Docker image URL (e.g., ghcr.io/org/app:tag)", help="Image ref")
@click.option("--container-path", prompt="Internal container volume path (e.g., /config)", help="Path inside container")
# Traefik question should appear right above Profiles
@click.option("--expose/--no-expose", default=False, prompt="Expose via Traefik (adds networks: t2_proxy and labels)?", help="Expose through Traefik")
@click.option("--internal-port", type=int, prompt="Container internal port", help="Container internal port")
@click.option("--external-port", type=int, default=None, callback=external_port_cb, help="LAN port (when not exposing)")
@click.option("--profiles", type=CommaList(), default="", prompt="Profiles (comma-separated, optional)", show_default=False, help="Compose profiles")
@click.option("--restart", default=None, callback=restart_select_cb, help="Container restart policy")
@click.option("--middleware-chain", default=None, callback=middleware_chain_cb, help="Traefik middleware chain")
def append(
    compose_path: Path,
    name: str,
    image: str,
    container_path: str,
    expose: bool,
    internal_port: int,
    external_port: Optional[int],
    profiles: List[str],
    restart: Optional[str],
    middleware_chain: Optional[str],
):
    """Append a service to an existing compose file."""
    svc = Service(
        name=name,
        image=image,
        container_path=container_path,
        profiles=profiles,
        restart=restart or "unless-stopped",
        expose=expose,
        internal_port=internal_port,
        external_port=external_port,
        middleware_chain=middleware_chain,
        # environment defaults are applied in the Service model (PUID/PGID/TZ)
    )

    # Preview of appended snippet (only YAML is green)
    snippet = {svc.name: svc.to_compose_value()}
    click.echo("\nThis service will be appended to the selected compose file:")
    click.secho(dump_yaml_str(snippet), fg="green")

    if not click.confirm(f"Proceed to append to {compose_path}?", default=True):
        click.secho("No changes made.", fg="yellow")
        return

    try:
        upsert_service_in_file(compose_path, svc)
    except SystemExit as e:
        raise click.ClickException(str(e))

    click.secho(f"Service '{svc.name}' written to {compose_path}", fg="green")


if __name__ == "__main__":
    cli()

