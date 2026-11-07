"""Build tiny, valid PDFs in memory so tests never need fixture files or the network."""

from pathlib import Path


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _content_stream(lines: list[str]) -> bytes:
    if not lines:
        return b""
    ops = ["BT", "/F1 12 Tf", "14 TL", "72 720 Td"]
    for i, line in enumerate(lines):
        if i:
            ops.append("T*")
        ops.append(f"({_escape(line)}) Tj")
    ops.append("ET")
    return "\n".join(ops).encode("latin-1")


def make_pdf(pages: list[list[str]] | None = None, info: dict[str, str] | None = None) -> bytes:
    """Return the bytes of a PDF with one page per entry in `pages` (each a list of text lines).

    A page with no lines has no text layer, like a scanned image. `info` sets the document
    information dictionary, e.g. {"Title": "...", "Author": "...", "CreationDate": "D:2021..."}.
    """
    pages = pages if pages is not None else [["Hello world"]]
    objects: list[bytes] = []  # object n is objects[n - 1]

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog = add(b"")  # filled in once the page tree exists
    pages_obj = add(b"")
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids = []
    for lines in pages:
        stream = _content_stream(lines)
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        page_ids.append(
            add(
                (
                    f"<< /Type /Page /Parent {pages_obj} 0 R /MediaBox [0 0 612 792] "
                    f"/Resources << /Font << /F1 {font} 0 R >> >> /Contents {content} 0 R >>"
                ).encode()
            )
        )
    kids = " ".join(f"{p} 0 R" for p in page_ids)
    objects[pages_obj - 1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode()
    objects[catalog - 1] = f"<< /Type /Catalog /Pages {pages_obj} 0 R >>".encode()
    info_id = None
    if info:
        entries = " ".join(f"/{k} ({_escape(v)})" for k, v in info.items())
        info_id = add(f"<< {entries} >>".encode("latin-1"))

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    trailer = f"<< /Size {len(objects) + 1} /Root {catalog} 0 R"
    if info_id:
        trailer += f" /Info {info_id} 0 R"
    out += f"trailer\n{trailer} >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def write_pdf(path: Path, pages: list[list[str]] | None = None, info: dict[str, str] | None = None) -> Path:
    path.write_bytes(make_pdf(pages, info))
    return path


# A short paper with a recognizable abstract and introduction
SAMPLE_PAPER_PAGES = [
    [
        "Attention Is Mostly What You Need",
        "Ada Lovelace, Alan Turing",
        "Abstract",
        "We study sparse attention for long docu-",
        "ments and show it matches dense attention.",
        "1 Introduction",
        "Transformers are widely used.",
    ],
    ["2 Method", "We propose a new sparse kernel."],
]
