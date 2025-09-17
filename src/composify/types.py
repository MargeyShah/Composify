from typing import Any, Dict, List, Optional
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict, field_validator, computed_field
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

# Shared YAML instance (round-trip capable for comment handling)
yaml_rt = YAML()
yaml_rt.preserve_quotes = True
yaml_rt.indent(mapping=2, sequence=2, offset=2)


class Service(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    image: str
    container_path: str
    profiles: List[str] = Field(default_factory=list)
    expose: bool = False
    service_port: int = 8084
    container_name: Optional[str] = None

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

    @field_validator("container_name", mode="after")
    @classmethod
    def _default_container_name(cls, v: Optional[str], info) -> str:
        if v:
            return v
        return info.data.get("name")

    @computed_field  # type: ignore[misc]
    @property
    def volumes(self) -> List[str]:
        return [f"${{DOCKERDIR}}/{self.name}:{self.container_path}"]

    @computed_field  # type: ignore[misc]
    @property
    def networks(self) -> Optional[List[str]]:
        return ["t2_proxy"] if self.expose else None

    @computed_field  # type: ignore[misc]
    @property
    def labels(self) -> Optional[Dict[str, str]]:
        if not self.expose:
            return None
        n = self.name
        return {
            "traefik.enable": "true",
            f"traefik.http.routers.{n}-rtr.entrypoints": "web-secure",
            f"traefik.http.routers.{n}-rtr.rule": f"Host(`{n}.${{DOMAINNAME}}`)",
            f"traefik.http.routers.{n}-rtr.middlewares": "chain-authelia@file",
            f"traefik.http.routers.{n}-rtr.service": f"{n}-svc",
            f"traefik.http.services.{n}-svc.loadbalancer.server.port": str(self.service_port),
        }

    def primary_profile_title(self) -> str:
        for p in self.profiles:
            if p.lower() != "all":
                return p[:1].upper() + p[1:]
        return self.name[:1].upper() + self.name[1:]

    def to_compose_value(self) -> Dict[str, Any]:
        return self.model_dump(
            exclude={"name"},
            exclude_none=True,
            by_alias=True,
        )


class ComposeStack(BaseModel):
    model_config = ConfigDict(extra="ignore")
    services: Dict[str, Service]

    @classmethod
    def new_with_service(cls, svc: Service) -> "ComposeStack":
        return cls(services={svc.name: svc})

    def add_or_replace(self, svc: Service, overwrite: bool) -> None:
        if svc.name in self.services and not overwrite:
            raise SystemExit(
                f"Service '{svc.name}' already exists. Use overwrite to replace it."
            )
        self.services[svc.name] = svc

    def to_compose_dict(self) -> Dict[str, Any]:
        return {"services": {name: svc.to_compose_value() for name, svc in self.services.items()}}
