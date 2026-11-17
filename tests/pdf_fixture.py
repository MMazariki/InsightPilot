"""Build small text PDFs for tests (one line of text per row, Helvetica)."""


def _escape(text):
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages, encrypt_note=False):
    """``pages`` is a list of pages, each a list of text lines. Returns bytes."""

    objects = []  # bodies, object number = index + 1

    def add(body):
        objects.append(body)
        return len(objects)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    pages_number = len(objects) + 1 + 2 * len(pages)  # reserved below
    page_numbers = []
    for lines in pages:
        stream = ["BT", "/F1 11 Tf", "14 TL", "50 780 Td"]
        for line in lines:
            stream.append(f"({_escape(line)}) Tj T*")
        stream.append("ET")
        data = "\n".join(stream).encode("latin-1")
        content = add(b"<< /Length %d >>\nstream\n" % len(data) + data + b"\nendstream")
        page_numbers.append(add(
            b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 842] "
            b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
            % (pages_number, font, content)))
    kids = b" ".join(b"%d 0 R" % n for n in page_numbers)
    assert add(b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(pages))) == pages_number
    catalog = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_number)

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, catalog, xref)
    return bytes(out)
