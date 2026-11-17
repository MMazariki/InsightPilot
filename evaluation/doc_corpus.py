"""Build the Documents-page evaluation corpus: project documents as PDFs.

The user's own PDFs are private and are not used for a committed test set.
Instead, four project documents are taken from a fixed git revision (so the
text and page numbers never change) and written as simple text PDFs, 48
wrapped lines per page:

    python -m evaluation.doc_corpus <output folder>
"""

import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REVISION = "c7100c4"
SOURCES = {
    "User_Guide.pdf": "README.md",
    "Evaluation_Report.pdf": "docs/EVALUATION.md",
    "Documents_Design.pdf": "docs/RAG_PLAN.md",
    "Voice_Input_Plan.pdf": "docs/VOICE_INPUT_PLAN.md",
}
LINES_PER_PAGE = 48
WIDTH = 95


def pages_of(text):
    lines = []
    for paragraph in text.split("\n"):
        paragraph = paragraph.replace("`", "").replace("**", "")
        paragraph = paragraph.encode("latin-1", "replace").decode("latin-1")
        lines += textwrap.wrap(paragraph, WIDTH) or [""]
    return [lines[i:i + LINES_PER_PAGE] for i in range(0, len(lines), LINES_PER_PAGE)]


def build(folder, revision=REVISION):
    from tests.pdf_fixture import make_pdf

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    built = {}
    for name, source in SOURCES.items():
        text = subprocess.run(["git", "show", f"{revision}:{source}"], cwd=ROOT,
                              capture_output=True, text=True, encoding="utf-8",
                              check=True).stdout
        pages = pages_of(text)
        (folder / name).write_bytes(make_pdf(pages))
        built[name] = pages
    return built


if __name__ == "__main__":
    out = build(sys.argv[1])
    for name, pages in out.items():
        print(name, len(pages), "pages")
