"""Filtros duros de recuperación: función pura, tipada y testeable. Nunca se concatenan a SQL."""
from dataclasses import asdict, dataclass
from typing import Literal

Flow = Literal["support", "sales"]


@dataclass(frozen=True)
class RetrievalFilter:
    audience: str
    allowed_client_types: tuple[str, ...]
    status: str
    language: str

    def to_dict(self) -> dict:
        return {**asdict(self), "allowed_client_types": list(self.allowed_client_types)}

    @classmethod
    def from_dict(cls, d: dict) -> "RetrievalFilter":
        return cls(d["audience"], tuple(d["allowed_client_types"]), d["status"], d["language"])


def build_filter(flow: Flow, client_type: str, lang: str) -> RetrievalFilter:
    if flow not in ("support", "sales"):
        raise ValueError(f"flow inválido: {flow!r}")
    return RetrievalFilter(
        audience=flow,
        allowed_client_types=(client_type, "common"),
        status="published",
        language=lang,
    )


def violates_filter(chunk: dict, f: RetrievalFilter) -> bool:
    """True si el chunk NO debería haberse recuperado con este filtro."""
    return chunk.get("audience") != f.audience or not (
        set(chunk.get("client_types", [])) & set(f.allowed_client_types)
    )
