from eco_kb.graph.nodes.generation import check_numeric_claims

CTX = ["Diluya EC-100 al 2% (20 ml por litro). Para suciedad intensa use 1:10 y 5 minutos. Error E04."]


def test_literal_passes():
    assert check_numeric_claims("Use EC-100 al 2% y 20 ml por litro, ratio 1:10 durante 5 minutos. Código E04.", CTX) == []


def test_invented_dilution_fails():
    assert "5%" in check_numeric_claims("Diluya al 5%.", CTX)


def test_invented_code_and_ratio_fail():
    miss = check_numeric_claims("El error E09 se resuelve con 1:20", CTX)
    assert "e09" in miss and "1:20" in miss


def test_spacing_normalized():
    assert check_numeric_claims("2 %", CTX) == []
    assert check_numeric_claims("20 ml", CTX) == []
