"""Dump per-page text from the docs/*.pdf papers into plain-text files."""
import pathlib
import sys

from pypdf import PdfReader

DOCS = pathlib.Path("/Users/liuzhanyu/workspace/RLProject/docs")
OUT = DOCS / "_extract"
OUT.mkdir(exist_ok=True)

for pdf in sorted(DOCS.glob("*.pdf")):
    reader = PdfReader(str(pdf))
    chunks = []
    for i, page in enumerate(reader.pages, 1):
        try:
            txt = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001 - keep going on bad pages
            txt = f"[extract error: {exc}]"
        chunks.append(f"\n\n===== [{pdf.stem}] PAGE {i}/{len(reader.pages)} =====\n{txt}")
    target = OUT / f"{pdf.stem}.txt"
    target.write_text("".join(chunks), encoding="utf-8")
    print(f"{pdf.name}: {len(reader.pages)} pages -> {target.name} ({target.stat().st_size} bytes)", file=sys.stderr)
