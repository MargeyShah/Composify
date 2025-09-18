import click
import secrets as pysecrets
import os
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
    list_root_network_subnets,
    pick_unused_subnet,
    upsert_network_in_main_compose,
    attach_network_to_services,
    derive_app_name_from_compose,
    upsert_root_secrets,
    ensure_secret_files,
)
home_dir = os.getenv('HOME')
DEFAULT_ROOT = Path(f"{home_dir}/docker")
SECRETSDIR_PATH = DEFAULT_ROOT / "secrets"  # ~/docker/secrets
DEFAULT_STACKS_DIR = DEFAULT_ROOT / "stacks"
MAIN_COMPOSE = DEFAULT_ROOT / "docker-compose.yml"
MIDDLEWARE_CHAINS_FILE = Path(f"{home_dir}/docker/apps/traefik2/rules/middleware-chains.yml")

RESTART_CHOICES = ["always", "unless-stopped", "on-failure", "no"]
DB_VIEWER_COMPOSE = DEFAULT_STACKS_DIR / "db-viewer.yml"
DB_VIEWER_SERVICE = "pga"

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

def db_service_name_cb(ctx: click.Context, param: click.Option, value: Optional[str]) -> str:
    # Compute <app_name> from the compose path
    compose_path: Path | None = ctx.params.get("compose_path")
    if not compose_path:
        # choose_compose_cb guarantees compose_path will be set before this callback runs,
        # but guard anyway
        compose_path = choose_compose_cb(ctx, param, None)

    app_name = derive_app_name_from_compose(compose_path, DEFAULT_STACKS_DIR)
    default_name = f"{app_name}-db"

    # Prompt with default
    name = (value or "").strip()
    if not name:
        name = click.prompt("DB service/container name", default=default_name, show_default=True).strip()

    # Uniqueness check (same logic as unique_name_cb)
    try:
        existing = set(get_existing_service_names(compose_path))
    except SystemExit as e:
        raise click.ClickException(str(e))
    if name in existing:
        raise click.BadParameter(
            click.style(f"A service named '{name}' already exists in {compose_path}.", fg='red')
        )
    return name

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
@click.option("--middleware-chain", default=None, callback=middleware_chain_cb, help="Traefik middleware chain")
@click.option("--profiles", type=CommaList(), default="", prompt="Profiles (comma-separated, optional)", show_default=False, help="Compose profiles")
@click.option("--restart", default=None, callback=restart_select_cb, help="Container restart policy")
def new(
    folder: str,
    name: str,
    image: str,
    container_path: str,
    expose: bool,
    internal_port: int,
    external_port: Optional[int],
    middleware_chain: Optional[str],
    profiles: List[str],
    restart: Optional[str],
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

    # offer to create a DB service for this app now
    if click.confirm("Would you like to create a Postgres DB service for this app now?", default=False):
        # Use <container_name>_db by default; avoid double _db
        base = name.strip()
        db_service_name = base if base.endswith("-db") else f"{base}-db"

        # Invoke the existing create_db command programmatically with sensible defaults.
        # Users will still be prompted inside create_db for DB creds and attachments.
        ctx = click.get_current_context()
        try:
            ctx.invoke(
                create_db,  # make sure create_db is defined in the same module or imported above
                compose_path=stack_compose,
                db_service_name=db_service_name,
                image="docker.io/library/postgres:16-alpine",
                container_path="/var/lib/postgresql/data",
                network_name=None,
            )
        except click.ClickException as e:
            # surface any handled errors nicely
            raise
        except Exception as e:
            # convert unexpected errors
            raise click.ClickException(str(e))


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
@click.option("--middleware-chain", default=None, callback=middleware_chain_cb, help="Traefik middleware chain")
@click.option("--profiles", type=CommaList(), default="", prompt="Profiles (comma-separated, optional)", show_default=False, help="Compose profiles")
@click.option("--restart", default=None, callback=restart_select_cb, help="Container restart policy")
def append(
    compose_path: Path,
    name: str,
    image: str,
    container_path: str,
    expose: bool,
    internal_port: int,
    external_port: Optional[int],
    middleware_chain: Optional[str],
    profiles: List[str],
    restart: Optional[str],
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

    # offer to create a DB service for this app now
    if click.confirm("Would you like to create a Postgres DB service for this app now?", default=False):
        # Use <container_name>_db by default; avoid double _db
        base = name.strip()
        db_service_name = base if base.endswith("-db") else f"{base}-db"

        # Invoke the existing create_db command programmatically with sensible defaults.
        # Users will still be prompted inside create_db for DB creds and attachments.
        ctx = click.get_current_context()
        try:
            ctx.invoke(
                create_db,  # make sure create_db is defined in the same module or imported above
                compose_path=compose_path,
                db_service_name=db_service_name,
                image="docker.io/library/postgres:16-alpine",
                container_path="/var/lib/postgresql/data",
                network_name=None,
            )
        except click.ClickException as e:
            # surface any handled errors nicely
            raise
        except Exception as e:
            # convert unexpected errors
            raise click.ClickException(str(e))


@cli.command()
@click.option(
    "--compose",
    "compose_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    callback=choose_compose_cb,
    help=f"Compose YAML file (under {DEFAULT_STACKS_DIR}). If omitted, you'll choose from a list.",
)
@click.option(
    "--db-name",
    "db_service_name",
    prompt=False,
    callback=db_service_name_cb,  # existing callback (defaults to <app>_db based on compose path)
    help="DB service/container name (defaults to <app>-db based on the compose path)",
)
@click.option(
    "--image",
    default=None,
    show_default=False,
    help="Full DB image ref (overrides --pg-tag). Example: docker.io/library/postgres:16-alpine",
)
@click.option(
    "--pg-tag",
    "pg_tag",
    default=None,
    show_default=False,
    help="Postgres image tag to use when --image is not provided (e.g., 16-alpine)",
)
@click.option(
    "--container-path",
    default="/var/lib/postgresql/data",
    show_default=True,
    help="Internal data path to mount (e.g., /var/lib/postgresql/data)",
)
@click.option(
    "--network-name",
    default=None,
    help="Override network name (default later based on the chosen prefix; avoids double -db)",
)
def create_db(
    compose_path: Path,
    db_service_name: str,
    image: Optional[str],
    pg_tag: Optional[str],
    container_path: str,
    network_name: Optional[str],
):
    """
    Create a Postgres DB service, add a dedicated network in root compose, attach other services,
    and scaffold secrets (files + root secrets block).
    """
    # Effective image: user-specified --image OR prompt for tag
    if not image:
        pg_tag = pg_tag or click.prompt("Postgres image tag", default="16-alpine", show_default=True)
    effective_image = image or f"docker.io/library/postgres:{pg_tag}"

    # Let user choose additional services to attach BEFORE we derive prefix
    try:
        existing_services = [s for s in get_existing_service_names(compose_path) if s != db_service_name]
    except SystemExit as e:
        raise click.ClickException(str(e))

    attach_selected: List[str] = []
    if existing_services:
        click.echo(f"\nSelect additional services from {compose_path} to attach to the DB network:")
        for i, it in enumerate(existing_services, start=1):
            click.echo(f"  {i}. {it}")
        raw = click.prompt("Enter numbers (comma-separated), or 0 to skip", default="0").strip()
        if raw and raw != "0":
            try:
                idxs = [int(x) for x in raw.split(",") if x.strip()]
                seen = set()
                for i in idxs:
                    if 1 <= i <= len(existing_services):
                        name = existing_services[i - 1]
                        if name not in seen:
                            attach_selected.append(name)
                            seen.add(name)
            except ValueError:
                click.secho("Invalid input; skipping additional attachments.", fg="yellow")

    # Choose an app prefix for names (network/service/secrets)
    # Default to the single selected service if exactly one chosen, else derive from compose path
    suggested_prefix = attach_selected[0] if len(attach_selected) == 1 else derive_app_name_from_compose(compose_path, DEFAULT_STACKS_DIR)
    prefix = click.prompt("App prefix for DB (used for network/service/secrets)", default=suggested_prefix, show_default=True).strip()
    app_name = prefix
    pretty_app = app_name.replace("-", " ").replace("_", " ").title()

    # Optionally align the DB service/container name with the chosen prefix
    desired_db_service = app_name if app_name.endswith("-db") else f"{app_name}-db"
    if db_service_name != desired_db_service:
        if click.confirm(f"Rename DB service to '{desired_db_service}' to match the prefix?", default=True):
            db_service_name = desired_db_service

    # Resolve network name (avoid double _db); prefer user override if provided
    if network_name:
        net_name = network_name.strip()
    else:
        base = db_service_name.strip()
        net_name = base if base.endswith("-db") else f"{base}-db"

    # Determine if we can update stacks/db-viewer.yml
    db_viewer_can_update = False
    if DB_VIEWER_COMPOSE.exists():
        try:
            db_services = set(get_existing_service_names(DB_VIEWER_COMPOSE))
            db_viewer_can_update = DB_VIEWER_SERVICE in db_services
        except SystemExit:
            db_viewer_can_update = False

    # 1) Determine an unused subnet for the new network
    try:
        existing_subnets = list_root_network_subnets(MAIN_COMPOSE)
        chosen_subnet = pick_unused_subnet(existing_subnets)
    except SystemExit as e:
        raise click.ClickException(str(e))

    # 2) Prompt for DB creds with sensible defaults (no confirmation for password)
    default_user = app_name
    default_db = app_name
    gen_pw = pysecrets.token_urlsafe(24)

    click.echo("\nDatabase credentials (press Enter to accept defaults):")
    db_user = click.prompt("Database user", default=default_user)
    db_password = click.prompt(
        "Database password (auto-generated if left as default)",
        default=gen_pw,
        hide_input=True,
        confirmation_prompt=False,
        show_default=False,
    )
    db_database = click.prompt("Database name", default=default_db)

    # 3) Prepare Postgres secrets names and file contents using the chosen prefix
    img_lower = effective_image.lower()
    if "postgres" not in img_lower:
        click.secho("Warning: secrets scaffolding currently targets Postgres images. Proceeding anyway.", fg="yellow")

    secret_user = f"{app_name}_postgresql_user"
    secret_password = f"{app_name}_postgresql_password"
    secret_db = f"{app_name}_postgresql_db"
    secret_names = [secret_db, secret_user, secret_password]  # alphabetical insertion handled by util

    secret_contents: Dict[str, str] = {
        secret_user: db_user,
        secret_password: db_password,
        secret_db: db_database,
    }

    # 4) Previews
    click.echo(f"\nMain compose: {MAIN_COMPOSE}")
    click.echo("Network to add:")
    net_preview = {
        "networks": {
            net_name: {
                "name": net_name,
                "internal": True,
                "ipam": {"config": [{"subnet": chosen_subnet}]},
            }
        }
    }
    click.secho(dump_yaml_str(net_preview), fg="green")

    internal_port = 5432 if "postgres" in img_lower else 3306
    env: Dict[str, str] = {
        "PUID": "$PUID",
        "PGID": "$PGID",
        "TZ": "$TZ",
        "POSTGRES_USER_FILE": f"/run/secrets/{secret_user}",
        "POSTGRES_PASSWORD_FILE": f"/run/secrets/{secret_password}",
        "POSTGRES_DB_FILE": f"/run/secrets/{secret_db}",
    }

    svc = Service(
        name=db_service_name,
        image=effective_image,
        container_path=container_path,
        profiles=[],  # 'all' added by validator
        restart="unless-stopped",
        expose=False,
        internal_port=internal_port,
        networks_extra=[net_name],
        environment=env,
        secrets=[secret_db, secret_user, secret_password],
    )

    svc_snippet = {svc.name: svc.to_compose_value()}
    click.echo(f"\nThis DB service will be appended to {compose_path}:")
    click.secho(dump_yaml_str(svc_snippet), fg="green")

    # 5) Confirm
    click.echo("\nAbout to:")
    click.echo(f"- Add network '{net_name}' with subnet {chosen_subnet} to {MAIN_COMPOSE}")
    click.echo(f"- Create secret files in {SECRETSDIR_PATH}: {', '.join(secret_names)}")
    click.echo(f"- Add secrets entries to {MAIN_COMPOSE} (alphabetical) with comment '# {pretty_app} Secrets'")
    click.echo(f"- Append DB service '{db_service_name}' to {compose_path}")
    if attach_selected:
        click.echo(f"- Attach network '{net_name}' to: {', '.join(attach_selected)}")
    else:
        click.echo("- No additional services selected for network attachment")
    if db_viewer_can_update:
        click.echo(f"- Attach network '{net_name}' to {DB_VIEWER_SERVICE} in {DB_VIEWER_COMPOSE}")
    else:
        click.echo(f"- Note: {DB_VIEWER_COMPOSE} missing or service '{DB_VIEWER_SERVICE}' not found; will skip updating it")

    if not click.confirm("\nProceed?", default=True):
        click.secho("No changes made.", fg="yellow")
        return

    # 6) Apply changes
    try:
        # Root network
        upsert_network_in_main_compose(MAIN_COMPOSE, net_name, chosen_subnet, internal=True)

        # Secrets: create files (sudo) then add to root compose
        ensure_secret_files(SECRETSDIR_PATH, secret_contents)
        upsert_root_secrets(MAIN_COMPOSE, pretty_app, [secret_db, secret_user, secret_password])

        # Append DB service
        upsert_service_in_file(compose_path, svc)

        # Attach network to selected services in chosen compose
        attach_network_to_services(compose_path, attach_selected, net_name)

        # Also attach to pga in stacks/db-viewer.yml if available
        if db_viewer_can_update:
            attach_network_to_services(DB_VIEWER_COMPOSE, [DB_VIEWER_SERVICE], net_name)
            click.secho(f"Attached '{net_name}' to {DB_VIEWER_SERVICE} in {DB_VIEWER_COMPOSE}", fg="green")

    except SystemExit as e:
        raise click.ClickException(str(e))

    # 7) Done
    click.secho(f"\nNetwork '{net_name}' added (or verified) in {MAIN_COMPOSE}", fg="green")
    click.secho("Secrets files created and entries added to root compose", fg="green")
    click.secho(f"DB service '{db_service_name}' written to {compose_path}", fg="green")
    if attach_selected:
        click.secho(f"Attached '{net_name}' to: {', '.join(attach_selected)}", fg="green")
    else:
        click.secho("No additional services were modified.", fg="yellow")@cli.command()


@cli.command()
@click.argument("name")
def create_secret(name: str):
    """
    Create a secret file in $SECRETSDIR and register it under 'secrets:' in the root compose (alphabetically).
    Usage: composify create_secret <file_name>
    """
    def _pretty_name(name: str) -> str:
        return name.replace("-", " ").replace("_", " ").title()
    secret_name = name.strip().replace('-', '_')
    if not secret_name or any(ch in secret_name for ch in ("/", "\\")):
        raise click.ClickException("Secret name must be a simple file name (no path separators).")

    # Generate a strong secret value
    value = pysecrets.token_urlsafe(40)

    # 1) Create the secret file under $SECRETSDIR (no overwrite; requires sudo)
    try:
        ensure_secret_files(SECRETSDIR_PATH, {secret_name: value})
    except SystemExit as e:
        raise click.ClickException(str(e))

    # 2) Register the secret in root compose (alphabetical), with a "# <Name> Secrets" comment header
    display = _pretty_name(secret_name)
    try:
        upsert_root_secrets(MAIN_COMPOSE, display, [secret_name])
    except SystemExit as e:
        raise click.ClickException(str(e))

    click.secho(f"Secret '{secret_name}' created at {SECRETSDIR_PATH}/{secret_name}", fg="green")
    click.secho(f"Added '{secret_name}' to 'secrets:' in {MAIN_COMPOSE}", fg="green")

if __name__ == "__main__":
    cli()

