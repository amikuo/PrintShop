"""Disposable PDF visual samples for calibration and inclusive-only totals."""
from pathlib import Path
from app.pricing import calibrate
from app.pdf_export import build_document_pdf, build_project_pdf

root = Path("tmp/pdfs")
root.mkdir(parents=True, exist_ok=True)
source = [
    {"product_name":"精裝書", "quantity":20, "variant_count":1, "unit_price":350, "unit":"本", "material":"封面／250p 霧膜\n內頁／80g 道林紙"},
    {"product_name":"膠裝書", "quantity":10, "variant_count":1, "unit_price":91, "unit":"本"},
]
result = calibrate({"items":source, "target_total":7910, "adjustment_index":1})
items = [{**i, "unit_price":r["unit_price"], "subtotal":r["subtotal"]} for i,r in zip(source,result["items"])]
record = {"customer_name":"學校示範", "created_at":"2026-09-11", "quote_number":"Q26091101", "order_number":"26091101", "tax_mode":"calibrated"}
for kind in ("quote", "order"):
    (root / f"calibrated_{kind}.pdf").write_bytes(build_document_pdf(kind,record,items,[],
        subtotal=result["subtotal"],tax_amount=result["tax_amount"],total=result["total"]))
record["tax_mode"] = "inclusive"
gross_items = [{**i, "subtotal":i["quantity"] * i["unit_price"]} for i in source]
(root / "inclusive_total_only.pdf").write_bytes(build_document_pdf("quote",record,gross_items,[],subtotal=7533,tax_amount=377,total=7910))
(root / "calibrated_project.pdf").write_bytes(build_project_pdf({**record,"project_name":"印刷專案"},
    [{"name":"教務處", "items":items * 6,"subtotal":7533 * 6}], subtotal=7533 * 6,tax_amount=377 * 6,total=7910 * 6))
