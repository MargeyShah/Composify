# from typing import Any, Dict, List, Optional
# from pathlib import Path
# from pydantic import BaseModel, Field, ConfigDict, field_validator, computed_field
# from ruamel.yaml import YAML
# from ruamel.yaml.comments import CommentedMap, CommentedSeq
#
# # Shared YAML instance (round-trip capable for comment handling)
# yaml_rt = YAML()
# yaml_rt.preserve_quotes = True
# yaml_rt.indent(mapping=2, sequence=2, offset=2)
#
# class Service(BaseModel):
#     model_config = ConfigDict(extra="ignore")
#
#     # Inputs (some are internal-only)
#     name: str
#     image: str
#     container_path: str              # internal-only; used to compute volumes
#     profiles: List[str] = Field(default_factory=list)
#     restart: Optional[str] = 'unless-stopped'
#     expose: bool = False             # internal-only; controls labels/networks vs ports
#     internal_port: int               # REQUIRED; used for labels/ports; not emitted
#     external_port: Optional[int] = None  # internal-only; only when expose=False; not emitted
#     middleware_chain: Optional[str] = None  # internal-only; only when expose=True; not emitted
#     container_name: Optional[str] = None    # emitted
#
#     # Normalize profiles: split commas, strip, dedupe; always include "all"
#     @field_validator("profiles", mode="before")
#     @classmethod
#     def _flatten_profiles(cls, v: Any) -> List[str]:
#         if v is None:
#             return []
#         items = [v] if isinstance(v, str) else list(v)
#         out: List[str] = []
#         for item in items:
#             for tok in str(item).split(","):
#                 t = tok.strip()
#                 if t:
#                     out.append(t)
#         return out
#
#     @field_validator("profiles", mode="after")
#     @classmethod
#     def _ensure_all_and_dedupe(cls, v: List[str]) -> List[str]:
#         v = v + ["all"]
#         seen: set[str] = set()
#         out: List[str] = []
#         for p in v:
#             if p not in seen:
#                 seen.add(p)
#                 out.append(p)
#         return out
#
#     # Default container_name to name
#     @field_validator("container_name", mode="after")
#     @classmethod
#     def _default_container_name(cls, v: Optional[str], info) -> str:
#         return v or info.data.get("name")
#
#     # Computed compose fields (emitted in YAML)
#     @computed_field  # type: ignore[misc]
#     @property
#     def volumes(self) -> List[str]:
#         return [f"${{DOCKERDIR}}/{self.name}:{self.container_path}"]
#
#     @computed_field  # type: ignore[misc]
#     @property
#     def networks(self) -> Optional[List[str]]:
#         # Only when exposing via Traefik
#         return ["t2_proxy"] if self.expose else None
#
#     @computed_field  # type: ignore[misc]
#     @property
#     def labels(self) -> Optional[Dict[str, str]]:
#         if not self.expose:
#             return None
#         n = self.name
#         labels: Dict[str, str] = {
#             "traefik.enable": "true",
#             f"traefik.http.routers.{n}-rtr.entrypoints": "web-secure",
#             f"traefik.http.routers.{n}-rtr.rule": f"Host(`{n}.${{DOMAINNAME}}`)",
#             f"traefik.http.routers.{n}-rtr.service": f"{n}-svc",
#             f"traefik.http.services.{n}-svc.loadbalancer.server.port": str(self.internal_port),
#         }
#         if self.middleware_chain:
#             labels[f"traefik.http.routers.{n}-rtr.middlewares"] = f"{self.middleware_chain}@file"
#         return labels
#
#     @computed_field  # type: ignore[misc]
#     @property
#     def ports(self) -> Optional[List[str]]:
#         # Only when NOT exposing via Traefik; map LAN:container
#         if self.expose:
#             return None
#         ext = self.external_port if self.external_port is not None else self.internal_port
#         return [f"{ext}:{self.internal_port}"]
#
#     def primary_profile_title(self) -> str:
#         for p in self.profiles:
#             if p.lower() != "all":
#                 return p[:1].upper() + p[1:]
#         return self.name[:1].upper() + self.name[1:]
#
#     def to_compose_value(self) -> Dict[str, Any]:
#         # Exclude internal-only fields so they never appear in YAML
#         return self.model_dump(
#             exclude={
#                 "name",
#                 "container_path",
#                 "expose",
#                 "internal_port",
#                 "external_port",
#                 "middleware_chain",
#             },
#             exclude_none=True,
#             by_alias=True,
#         )

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator, computed_field, ConfigDict

class Service(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Inputs (some are internal-only)
    name: str
    image: str
    container_path: str              # internal-only; used to compute volumes
    profiles: List[str] = Field(default_factory=list)
    restart: Optional[str] = "unless-stopped"
    expose: bool = False             # internal-only; controls labels/networks vs ports
    internal_port: int               # REQUIRED; used for labels/ports; not emitted
    external_port: Optional[int] = None  # internal-only; only when expose=False; not emitted
    middleware_chain: Optional[str] = None  # internal-only; only when expose=True; not emitted
    container_name: Optional[str] = None    # emitted
    environment: Dict[str, str] = Field(
        default_factory=lambda: {"PUID": "$PUID", "PGID": "$PGID", "TZ": "$TZ"}
    )

    # Normalize profiles: split commas, strip, dedupe; always include "all"
    @field_validator("profiles", mode="before")
    @classmethod
    def _flatten_profiles(cls, v: Any) -> List[str]:
        if v is None:
            return []
        items = [v] if isinstance(v, str) else list(v)
        out: List[str] = []
        for item in items:
            for tok in str(item).split(","):
                t = tok.strip()
                if t:
                    out.append(t)
        return out

    @field_validator("profiles", mode="after")
    @classmethod
    def _ensure_all_and_dedupe(cls, v: List[str]) -> List[str]:
        v = v + ["all"]
        seen: set[str] = set()
        out: List[str] = []
        for p in v:
            if p not in seen:
                seen.add(p)
                out.append(p)
        return out

    # Default container_name to name
    @field_validator("container_name", mode="after")
    @classmethod
    def _default_container_name(cls, v: Optional[str], info) -> str:
        return v or info.data.get("name")

    # Computed compose fields (emitted in YAML)
    @computed_field  # type: ignore[misc]
    @property
    def volumes(self) -> List[str]:
        return [f"${{DOCKERDIR}}/{self.name}:{self.container_path}"]

    @computed_field  # type: ignore[misc]
    @property
    def networks(self) -> Optional[List[str]]:
        # Only when exposing via Traefik
        return ["t2_proxy"] if self.expose else None

    @computed_field  # type: ignore[misc]
    @property
    def labels(self) -> Optional[Dict[str, str]]:
        if not self.expose:
            return None
        n = self.name
        labels: Dict[str, str] = {
            "traefik.enable": "true",
            f"traefik.http.routers.{n}-rtr.entrypoints": "web-secure",
            f"traefik.http.routers.{n}-rtr.rule": f"Host(`{n}.${{DOMAINNAME}}`)",
            f"traefik.http.routers.{n}-rtr.service": f"{n}-svc",
            f"traefik.http.services.{n}-svc.loadbalancer.server.port": str(self.internal_port),
        }
        if self.middleware_chain:
            labels[f"traefik.http.routers.{n}-rtr.middlewares"] = f"{self.middleware_chain}@file"
        return labels

    @computed_field  # type: ignore[misc]
    @property
    def ports(self) -> Optional[List[str]]:
        # Only when NOT exposing via Traefik; map LAN:container
        if self.expose:
            return None
        ext = self.external_port if self.external_port is not None else self.internal_port
        return [f"{ext}:{self.internal_port}"]

    def primary_profile_title(self) -> str:
        for p in self.profiles:
            if p.lower() != "all":
                return p[:1].upper() + p[1:]
        return self.name[:1].upper() + self.name[1:]

    def to_compose_value(self) -> Dict[str, Any]:
        # Build a normal dict in desired order (insertion order preserved in Python 3.7+)
        out: Dict[str, Any] = {}
        out["image"] = self.image
        out["container_name"] = self.container_name or self.name
        if self.restart is not None:
            out["restart"] = self.restart  # e.g., "unless-stopped"
        if self.profiles:
            out["profiles"] = self.profiles
        if self.expose:
            nets = self.networks
            if nets:
                out["networks"] = nets
        out["volumes"] = self.volumes
        if self.environment:
            out["environment"] = self.environment
        if self.expose:
            lbls = self.labels
            if lbls:
                out["labels"] = lbls
        else:
            prts = self.ports
            if prts:
                out["ports"] = prts
        return out

