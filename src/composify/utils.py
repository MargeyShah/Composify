
from pathlib import Path
from typing import Any, Dict, List
from composify import Service
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

# Shared YAML instance (round-trip capable for comment handling)
yaml_rt = YAML()
yaml_rt.preserve_quotes = True
yaml_rt.indent(mapping=2, sequence=2, offset=2)
# ---------------------------
# File I/O helpers (surgical updates)
# ---------------------------

def list_yaml_files(root: Path) -> List[Path]:
    return sorted(p for p in root.rglob("*.yml") if p.is_file())


def upsert_service_in_file(compose_path: Path, svc: Service, overwrite: bool) -> None:
    """
    Load compose_path, ensure top-level services: exists, and upsert the given service.
    Only the targeted service key is updated; other services are preserved.
    """
    if not compose_path.exists():
        raise SystemExit(f"Compose file not found: {compose_path}")

    data = yaml_rt.load(compose_path.read_text(encoding="utf-8")) or CommentedMap()
    if not isinstance(data, CommentedMap):
        data = CommentedMap()

    services = data.get("services")
    if services is None:
        raise SystemExit(f"{compose_path} has no top-level 'services:'")
    if not isinstance(services, (dict, CommentedMap)):
        raise SystemExit(f"Top-level 'services' is not a mapping in {compose_path}")

    if svc.name in services and not overwrite:
        raise SystemExit(
            f"Service '{svc.name}' already exists in {compose_path}. Use overwrite to replace it."
        )

    services[svc.name] = svc.to_compose_value()

    with compose_path.open("w", encoding="utf-8") as f:
        yaml_rt.dump(data, f)


def write_new_stack_file(stack_compose: Path, svc: Service, overwrite: bool) -> None:
    stack_compose.parent.mkdir(parents=True, exist_ok=True)
    if stack_compose.exists() and not overwrite:
        raise SystemExit(f"{stack_compose} already exists. Use overwrite to replace it.")

    data = CommentedMap()
    services = CommentedMap()
    services[svc.name] = svc.to_compose_value()
    data["services"] = services

    with stack_compose.open("w", encoding="utf-8") as f:
        yaml_rt.dump(data, f)


def load_main_compose(main_compose: Path) -> CommentedMap:
    data = yaml_rt.load(main_compose.read_text(encoding="utf-8")) if main_compose.exists() else CommentedMap()
    if not isinstance(data, CommentedMap):
        data = CommentedMap()
    return data


def append_to_include_with_comment(main_compose: Path, rel_include_path: str, comment_text: str) -> bool:
    data = load_main_compose(main_compose)

    include = data.get("include")
    if include is None:
        include = CommentedSeq()
        data["include"] = include
    if not isinstance(include, CommentedSeq):
        raise SystemExit(f"Top-level 'include' is not a list in {main_compose}")

    existing = [str(item) for item in include]
    if rel_include_path in existing:
        return False

    include.append(rel_include_path)
    idx = len(include) - 1
    try:
        include.yaml_set_comment_before_after_key(idx, before=comment_text)
    except Exception:
        pass

    with main_compose.open("w", encoding="utf-8") as f:
        yaml_rt.dump(data, f)
    return True

# ---------------------------
# Middleware chain discovery
# ---------------------------

def list_middleware_chains(file: Path) -> List[str]:
    """
    Parse a traefik middleware chains file and return the chain names under:
      http.middlewares.<chain-name>
    """
    if not file.exists():
        return []
    data = yaml_rt.load(file.read_text(encoding="utf-8")) or {}
    try:
        http = data.get("http") or {}
        mws = http.get("middlewares") or {}
        # Keys under 'middlewares' are the chain names
        names = [str(k) for k in mws.keys()]
        names.sort()
        return names
    except Exception:
        return []

# ---------------------------
# Pretty-print helpers
# ---------------------------

def dump_yaml_str(obj: Any) -> str:
    from io import StringIO
    buf = StringIO()
    yaml_rt.dump(obj, buf)
    return buf.getvalue()


def dump_include_only_str(data: CommentedMap) -> str:
    out = CommentedMap()
    if "include" in data:
        out["include"] = data["include"]
    return dump_yaml_str(out)


def simulate_include_after_append_str(main_compose: Path, rel_include_path: str, comment_text: str) -> str:
    data = load_main_compose(main_compose)

    include = data.get("include")
    if include is None:
        include = CommentedSeq()
        data["include"] = include
    if not isinstance(include, CommentedSeq):
        raise SystemExit(f"Top-level 'include' is not a list in {main_compose}")

    existing = [str(item) for item in include]
    if rel_include_path not in existing:
        include.append(rel_include_path)
        idx = len(include) - 1
        try:
            include.yaml_set_comment_before_after_key(idx, before=comment_text)
        except Exception:
            pass

    return dump_include_only_str(data)
