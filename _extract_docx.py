import os
os.chdir(r"f:\Myprogram\cursor\work\RAG")

from docx import Document

files = [
    r"data\支付政策+退款政策+隐私政策(1).docx",
    r"data\美越双市场独立站政策合规改造清单.docx",
]

for fp in files:
    print(f"\n{'='*60}")
    print(f"FILE: {fp}")
    print(f"{'='*60}")
    doc = Document(fp)
    print(f"Paragraphs: {len(doc.paragraphs)}, Tables: {len(doc.tables)}")

    print("\n--- Paragraphs ---")
    for i, para in enumerate(doc.paragraphs):
        if para.text.strip():
            print(f"[{i}] style={para.style.name}: {para.text[:200]}")

    print(f"\n--- Tables ({len(doc.tables)}) ---")
    for ti, table in enumerate(doc.tables):
        print(f"\n[Table {ti}] {len(table.rows)} rows x {len(table.columns)} cols")
        for ri, row in enumerate(table.rows[:10]):
            cells = [c.text.strip()[:100].replace('\n', ' ') for c in row.cells]
            print(f"  Row {ri}: {cells}")
        if len(table.rows) > 10:
            print(f"  ... ({len(table.rows) - 10} more rows)")
