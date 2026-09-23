"""Generate a disposable visual sample; run from the repository root."""
from pathlib import Path
from tests.test_variant_count import VariantCountTests
from app import database

test = VariantCountTests()
test.setUp()
try:
    conn = database.connect()
    ident = database.create_quote_from_payload(conn, {
        "customer_name": "學校測試", "tax_mode": "inclusive",
        "items": [{"product_name": "平裝書籍膠裝（加長封面折頁）", "material": "封面／250p 霧膜\n內頁／80g 道林紙", "note": "包括設計封面、協助配合排版、含稅", "quantity": 800, "unit_price": 187.5, "unit": "本", "size": "32K13x19cm"}],
    })
    conn.commit()
    conn.close()
    response = test.client.get(f"/quotes/{ident}/pdf")
    assert response.status_code == 200
    folder = Path("tmp/pdfs")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "inclusive.pdf").write_bytes(response.data)
    from app.pdf_export import build_project_pdf
    conn = database.connect()
    item = dict(conn.execute("SELECT * FROM quote_items WHERE quote_id=?", (ident,)).fetchone())
    conn.close()
    data = build_project_pdf(
        {"customer_name": "學校測試", "project_name": "書籍多頁測試", "created_at": "2026-09-10"},
        [{"name": "教務處（含稅單價／小計）", "order_number": "26091003", "items": [item] * 8, "subtotal": 1200000}],
        subtotal=1142857, tax_amount=57143, total=1200000,
    )
    (folder / "readability_project.pdf").write_bytes(data)
finally:
    test.tearDown()
