from io import BytesIO
import unittest
from pypdf import PdfReader
from app.pdf_export import build_document_pdf, build_project_pdf, _description_lines, _register_fonts


class ReadabilityTests(unittest.TestCase):
    def test_project_final_page_reserves_totals_space(self):
        from app.pdf_export import _build_project_group_layouts, _paginate_project_groups, TABLE_BODY_TOP, FINAL_ITEMS_BOTTOM, INTERMEDIATE_BOTTOM
        regular, bold = _register_fonts()
        item = {"product_name":"書籍", "material":"封面／250p\n內頁／80g", "quantity":20, "unit_price":333.3333, "subtotal":6667}
        for count in (1, 8, 12, 18, 30):
            layouts = _build_project_group_layouts([{"name":"教務處", "items":[item] * count, "subtotal":6667 * count}], regular, bold)
            pages = _paginate_project_groups(layouts, regular, bold)
            self.assertLessEqual(sum(r["height"] for r in pages[-1]), TABLE_BODY_TOP - FINAL_ITEMS_BOTTOM)
            for page in pages[:-1]:
                self.assertLessEqual(sum(r["height"] for r in page), TABLE_BODY_TOP - INTERMEDIATE_BOTTOM)
            self.assertEqual(sum(r.get("kind") == "item" for p in pages for r in p), count)

    def test_nine_point_minimum_notes_and_multipage(self):
        regular, bold = _register_fonts()
        item = {"product_name": "平裝書籍膠裝（加長封面折頁）",
                "material": "封面／250p 霧膜\n內頁／80g 道林紙",
                "note": "包括設計封面、協助配合排版、含稅",
                "quantity": 800, "unit": "本", "unit_price": 187.5,
                "subtotal": 150000, "size": "32K13x19cm"}
        lines = _description_lines(item, "", regular, bold, 152)
        self.assertTrue(any(line[0].startswith("備註：") for line in lines))
        self.assertTrue(all(line[2] >= 9 for line in lines))
        record = {"customer_name": "學校測試", "quote_number": "Q26091003",
                  "order_number": "26091003", "tax_mode": "inclusive"}
        samples = [build_document_pdf(kind, record, [item] * 18, [], subtotal=2571429, tax_amount=128571, total=2700000)
                   for kind in ("quote", "order")]
        samples.append(build_project_pdf(record, [{"name": "教務處", "items": [item] * 18, "subtotal": 2700000}], subtotal=2571429, tax_amount=128571, total=2700000))
        for data in samples:
            reader = PdfReader(BytesIO(data))
            self.assertGreater(len(reader.pages), 1)
            for page in reader.pages:
                sizes = []
                page.extract_text(visitor_text=lambda text, cm, tm, font, size: sizes.append(size) if text.strip() else None)
                self.assertTrue(sizes)
                self.assertGreaterEqual(min(sizes), 9)
