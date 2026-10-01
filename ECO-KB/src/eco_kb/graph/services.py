from dataclasses import dataclass
from typing import Any

from eco_kb.config import AppConfig
from eco_kb.llm import Embedder
from eco_kb.retrieval.store import ChunkStore


@dataclass
class Services:
    generator_llm: Any  # cualquier objeto con .with_structured_output(schema).invoke(messages)
    grader_llm: Any
    support_store: ChunkStore
    sales_store: ChunkStore
    embedder: Embedder
    config: AppConfig
    min_vector_score: float = 0.45
    max_ret: int = 2
    max_gen: int = 3
