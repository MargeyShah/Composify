
from pathlib import Path
from typing import Any, Dict, List
from composify import Service, ComposeStack
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

# Shared YAML instance (round-trip capable for comment handling)
yaml_rt = YAML()
yaml_rt.preserve_quotes = True
yaml_rt.indent(mapping=2, sequence=2, offset=2)
# ---------------------------
# File I/O and YAML helpers
# ---------------------------

def list_yaml_files(root: Path) -> List[Path]:
    """Recursively list *.yml files under the given root, sorted."""
    return sorted(p for p in root.rglob("*.yml") if p.is_file())


def load_stack(path: Path) -> ComposeStack:
    if not path.exists():
        raise SystemExit(f"Compose file not found: {path}")
    data = yaml_rt.load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or "services" not in data or not isinstance(data["services"], dict):
        raise SystemExit(f"{path} has no top-level 'services:'; create it via the New flow.")
    services: Dict[str, Service] = {}
    for name, val in data["services"].items():
        if isinstance(val, dict):
            services[name] = Service(name=name, **val)
    return ComposeStack(services=services)


def write_stack(path: Path, stack: ComposeStack) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml_rt.dump(stack.to_compose_dict(), f)


def load_main_compose(main_compose: Path) -> CommentedMap:
    data = yaml_rt.load(main_compose.read_text(encoding="utf-8")) if main_compose.exists() else CommentedMap()
    if not isinstance(data, CommentedMap):
        data = CommentedMap()
    return data


def dump_yaml_str(obj: Any) -> str:
    from io import StringIO
    buf = StringIO()
    yaml_rt.dump(obj, buf)
    return buf.getvalue()


def dump_include_only_str(data: CommentedMap) -> str:
    """Return YAML string containing only the 'include:' section (if present)."""
    out = CommentedMap()
    if "include" in data:
        out["include"] = data["include"]
    return dump_yaml_str(out)


def append_to_include_with_comment(main_compose: Path, rel_include_path: str, comment_text: str) -> bool:
    """Append rel_include_path in main_compose include: with a comment above. Create file if missing."""
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


def simulate_include_after_append_str(main_compose: Path, rel_include_path: str, comment_text: str) -> str:
    """Return the include: YAML as it would look after appending (no file write)."""
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
