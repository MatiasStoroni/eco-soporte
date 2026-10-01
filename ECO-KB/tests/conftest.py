import pytest

from eco_kb.config import load_config
from eco_kb.graph.services import Services


@pytest.fixture(scope="session")
def app_config():
    return load_config("config/clients.yaml", "config/safety.yaml")


class FakeStructured:
    def __init__(self, llm, schema):
        self.llm, self.schema = llm, schema

    def invoke(self, messages, *a, **k):
        self.llm.calls.append((self.schema.__name__, messages))
        queue = self.llm.script[self.schema]
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        return item(messages) if callable(item) else item


class FakeLLM:
    """script: {SchemaClass: [obj | callable(messages) -> obj, ...]}. El último valor se repite."""

    def __init__(self, script):
        self.script, self.calls = {k: list(v) for k, v in script.items()}, []

    def with_structured_output(self, schema):
        return FakeStructured(self, schema)

    def count(self, name):
        return sum(1 for n, _ in self.calls if n == name)


class FakeStore:
    def __init__(self, chunks):
        self.chunks, self.filters = chunks, []

    def search(self, vec, text, f):
        self.filters.append(f)
        return list(self.chunks)

    def catalog(self, f):
        return [{"title": "Doc", "product": "P", "sections": ["Sec"]}]


class FakeEmbedder:
    def embed_query(self, text):
        return [0.0] * 768

    def embed_queries(self, texts):
        return [[0.0] * 768 for _ in texts]

    def embed_documents(self, texts):
        return [[0.0] * 768 for _ in texts]


def chunk(cid, content, audience="support", client_types=("hotel",), vec_score=0.8, title="Doc"):
    return {"chunk_id": cid, "audience": audience, "source_id": cid.split("#")[0], "title": title,
            "section_path": "Sec", "content": content, "client_types": list(client_types),
            "vec_score": vec_score, "rrf": 0.03}


@pytest.fixture
def make_services(app_config):
    def _make(llm, support_chunks=(), sales_chunks=()):
        return Services(
            generator_llm=llm, grader_llm=llm,
            support_store=FakeStore(list(support_chunks)), sales_store=FakeStore(list(sales_chunks)),
            embedder=FakeEmbedder(), config=app_config, min_vector_score=0.45, max_ret=2, max_gen=3,
        )
    return _make
