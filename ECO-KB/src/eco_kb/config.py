"""Carga y validación de clients.yaml / safety.yaml. Fallar al arrancar si falta algo."""
import re
from pathlib import Path
from typing import get_args

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from eco_kb.graph.state import ClientType


class CTA(BaseModel):
    text: str = Field(min_length=1)
    base_url: str = Field(pattern=r"^https?://\S+$")


class Fallback(BaseModel):
    support: str = Field(min_length=1)
    sales: str = Field(min_length=1)


class ClientConfig(BaseModel):
    tone: str = Field(min_length=1)
    cta: CTA
    fallback: Fallback


class SafetyConfig(BaseModel):
    keywords: list[str]
    patterns: list[str]
    response: str = Field(min_length=1)

    @field_validator("patterns")
    @classmethod
    def _compile(cls, v: list[str]) -> list[str]:
        for p in v:
            re.compile(p)
        return v


class Capabilities(BaseModel):
    support: str = Field(min_length=1)
    sales: str = Field(min_length=1)


class DomainConfig(BaseModel):
    description: str = Field(min_length=1)
    capabilities: Capabilities
    glossary: list[str] = Field(default_factory=list)


class AppConfig(BaseModel):
    domain: DomainConfig
    clients: dict[str, ClientConfig]
    safety: SafetyConfig

    @model_validator(mode="after")
    def _all_client_types(self) -> "AppConfig":
        missing = set(get_args(ClientType)) - set(self.clients)
        if missing:
            raise ValueError(f"clients.yaml no define client_types: {sorted(missing)}")
        return self


def load_config(clients_path: str | Path, safety_path: str | Path) -> AppConfig:
    raw = yaml.safe_load(Path(clients_path).read_text(encoding="utf-8"))
    safety = yaml.safe_load(Path(safety_path).read_text(encoding="utf-8"))
    return AppConfig(domain=raw["domain"], clients=raw["clients"], safety=safety)
