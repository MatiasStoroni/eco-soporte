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


class HandoffConfig(BaseModel):
    """Derivación a humano (último recurso). Sin archivo: desactivada."""

    enabled: bool = False
    keywords: list[str] = Field(default_factory=list)
    patterns: list[str] = Field(default_factory=list)
    response: str = "Le aviso a una persona de nuestro equipo para que revise tu conversación."
    consecutive_fallbacks: int = Field(default=0, ge=0)
    fallback_reasons: list[str] = Field(default_factory=lambda: ["no_documents", "ungrounded", "answer_mismatch"])
    notice: str = "Le avisamos a una persona del equipo para que revise tu consulta."

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
    handoff: HandoffConfig = Field(default_factory=HandoffConfig)

    @model_validator(mode="after")
    def _all_client_types(self) -> "AppConfig":
        missing = set(get_args(ClientType)) - set(self.clients)
        if missing:
            raise ValueError(f"clients.yaml no define client_types: {sorted(missing)}")
        return self


def load_config(clients_path: str | Path, safety_path: str | Path,
                handoff_path: str | Path | None = None) -> AppConfig:
    raw = yaml.safe_load(Path(clients_path).read_text(encoding="utf-8"))
    safety = yaml.safe_load(Path(safety_path).read_text(encoding="utf-8"))
    handoff = yaml.safe_load(Path(handoff_path).read_text(encoding="utf-8")) if handoff_path else {}
    return AppConfig(domain=raw["domain"], clients=raw["clients"], safety=safety, handoff=handoff)
