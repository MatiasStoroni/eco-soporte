import pytest

from eco_kb.retrieval.filters import RetrievalFilter, build_filter, violates_filter


@pytest.mark.parametrize("flow", ["support", "sales"])
@pytest.mark.parametrize("ct", ["bodega", "hotel", "restaurante", "generic"])
def test_build_filter_table(flow, ct):
    f = build_filter(flow, ct, "es")
    assert f == RetrievalFilter(flow, (ct, "common"), "published", "es")
    assert RetrievalFilter.from_dict(f.to_dict()) == f


def test_invalid_flow():
    with pytest.raises(ValueError):
        build_filter("admin", "hotel", "es")


def test_violates_filter():
    f = build_filter("support", "hotel", "es")
    assert not violates_filter({"audience": "support", "client_types": ["common"]}, f)
    assert violates_filter({"audience": "sales", "client_types": ["hotel"]}, f)
    assert violates_filter({"audience": "support", "client_types": ["bodega"]}, f)
