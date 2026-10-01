from eco_kb.drive.pdf_clean import clean_pdf_pages

HEADER = "MANUAL OPERATIVO\nHOTELERÍA\nFECHA:\n3/07/2026\n"


def test_removes_repeated_headers_and_toc():
    toc = HEADER + "Contenido\nOBJETIVOS ........ . . . . . . . . 2\nALCANCE ........ . . . . . . . . 2\nPLAN ........ . . . . . . . . 3\n"
    pages = [toc, HEADER + "OBJETIVOS\nTexto uno que es suficientemente largo.", HEADER + "ALCANCE\nTexto dos que es suficientemente largo."]
    text, empty = clean_pdf_pages(pages)
    assert "MANUAL OPERATIVO" not in text and "...." not in text and "Contenido" not in text
    assert "Texto uno" in text and "Texto dos" in text
    assert empty == [1]


def test_reports_image_only_pages():
    pages = [HEADER + "texto de la página uno suficientemente largo", HEADER, HEADER, HEADER]
    text, empty = clean_pdf_pages(pages)
    assert empty == [2, 3, 4] and "página uno" in text


def test_word_per_line_is_joined():
    words = "Genera una solución estabilizada de ozono en agua ( SOW ) y elimina la grasa .".split() * 3
    page = "\n \n".join(words)
    text, _ = clean_pdf_pages([page])
    assert text.startswith("Genera una solución") and "(SOW)" in text and "\n" not in text


def test_ligatures_normalized():
    text, _ = clean_pdf_pages(["Limpieza de superﬁcies y eﬁcacia del producto en baños."])
    assert "superficies" in text and "eficacia" in text


def test_page_number_dropped():
    text, _ = clean_pdf_pages(["Una línea de texto bastante larga para pasar el mínimo.\n1"])
    assert text.endswith("mínimo.")
