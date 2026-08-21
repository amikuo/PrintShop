from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from .database import display_customer_contact, display_date


PAGE_WIDTH, PAGE_HEIGHT = A4
SIDEBAR_WIDTH = 155
MAIN_X = 168
MAIN_RIGHT = PAGE_WIDTH - 20
MAIN_WIDTH = MAIN_RIGHT - MAIN_X

TABLE_TOP = 640
TABLE_HEADER_HEIGHT = 23
TABLE_BODY_TOP = TABLE_TOP - TABLE_HEADER_HEIGHT
INTERMEDIATE_BOTTOM = 38
FINAL_ITEMS_BOTTOM = 252

NOTES_Y = 28
NOTES_HEIGHT = 82

INK = HexColor("#3e3a39")
MUTED = HexColor("#777777")
LIGHT_LINE = HexColor("#d8d8d8")
SIDEBAR = HexColor("#eeeeee")
SOFT_FILL = HexColor("#f4f4f4")

ASSET_DIR = Path(__file__).resolve().parent / "static" / "pdf"
LOGO_PATH = ASSET_DIR / "logo_form_dark.png"

FIXED_NOTES = (
    "不同設備顯色方式不同，成品與螢幕可能存在 ±10% 色差，屬正常範圍，客戶同意不以此為退貨理由。",
    "成品裁切、成套、對位等可能有 ±2mm 誤差，屬印刷加工正常範圍。",
    "以上已包含排版製作稿件。",
)

_FONT_NAMES: tuple[str, str] | None = None


def _register_fonts() -> tuple[str, str]:
    global _FONT_NAMES
    if _FONT_NAMES:
        return _FONT_NAMES

    windows = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    candidates = (
        (windows / "msjh.ttc", windows / "msjhbd.ttc"),
        (windows / "NotoSansTC-VF.ttf", windows / "NotoSansTC-VF.ttf"),
        (
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
        ),
        (
            Path("/System/Library/Fonts/PingFang.ttc"),
            Path("/System/Library/Fonts/PingFang.ttc"),
        ),
    )

    for regular_path, bold_path in candidates:
        if not regular_path.exists():
            continue
        if not bold_path.exists():
            bold_path = regular_path
        try:
            regular_kwargs = {"subfontIndex": 0} if regular_path.suffix.lower() == ".ttc" else {}
            bold_kwargs = {"subfontIndex": 0} if bold_path.suffix.lower() == ".ttc" else {}
            pdfmetrics.registerFont(TTFont("PrintShopTC", str(regular_path), **regular_kwargs))
            pdfmetrics.registerFont(TTFont("PrintShopTCBold", str(bold_path), **bold_kwargs))
            _FONT_NAMES = ("PrintShopTC", "PrintShopTCBold")
            return _FONT_NAMES
        except Exception:
            continue

    # Cross-platform fallback. Store Windows normally uses the embedded-subset
    # Microsoft JhengHei path above; this keeps development environments usable.
    pdfmetrics.registerFont(UnicodeCIDFont("MSung-Light"))
    _FONT_NAMES = ("MSung-Light", "MSung-Light")
    return _FONT_NAMES


def _value(row: Mapping[str, Any] | Any, key: str, default: Any = "") -> Any:
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        value = default
    return default if value is None else value


def _format_money(value: Any) -> str:
    amount = float(value or 0)
    if abs(amount - round(amount)) < 0.000001:
        return f"{amount:,.0f}"
    return f"{amount:,.2f}"


def _format_quantity(value: Any) -> str:
    quantity = float(value or 0)
    if abs(quantity - round(quantity)) < 0.000001:
        return f"{quantity:,.0f}"
    return f"{quantity:,.3f}".rstrip("0").rstrip(".")


def _format_date(value: Any, *, stored_utc: bool = False) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "—"
    if stored_utc:
        try:
            raw = display_date(raw)
        except Exception:
            pass
    return raw[:10].replace("-", "/")


def _wrap_text(text: Any, font_name: str, font_size: float, max_width: float) -> list[str]:
    source = str(text or "").replace("\r", "")
    if not source:
        return []
    lines: list[str] = []
    for paragraph in source.split("\n"):
        if not paragraph:
            lines.append("")
            continue
        current = ""
        for char in paragraph:
            candidate = current + char
            if current and pdfmetrics.stringWidth(candidate, font_name, font_size) > max_width:
                lines.append(current)
                current = char
            else:
                current = candidate
        if current:
            lines.append(current)
    return lines


def _draw_wrapped(
    pdf: canvas.Canvas,
    text: Any,
    x: float,
    y: float,
    width: float,
    *,
    font_name: str,
    font_size: float,
    line_height: float,
    max_lines: int | None = None,
) -> float:
    lines = _wrap_text(text, font_name, font_size, width)
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        tail = lines[-1]
        while tail and pdfmetrics.stringWidth(tail + "…", font_name, font_size) > width:
            tail = tail[:-1]
        lines[-1] = tail + "…"
    pdf.setFont(font_name, font_size)
    for line in lines:
        pdf.drawString(x, y, line)
        y -= line_height
    return y


def _description_lines(
    item: Mapping[str, Any] | Any,
    work_unit_name: str,
    regular_font: str,
    bold_font: str,
    width: float,
) -> list[tuple[str, str, float, float]]:
    styled: list[tuple[str, str, float, float]] = []
    product = str(_value(item, "product_name", "未命名品項")).strip() or "未命名品項"
    for line in _wrap_text(product, bold_font, 8.8, width):
        styled.append((line, bold_font, 8.8, 11))

    details: list[str] = []
    if work_unit_name:
        details.append(f"工作單位：{work_unit_name}")
    material = str(_value(item, "material", "")).strip()
    finishing = str(_value(item, "finishing", "")).strip()
    note = str(_value(item, "note", "")).strip()
    if material:
        details.append(f"材質：{material}")
    if finishing:
        details.append(f"加工：{finishing}")
    if note:
        details.append(f"備註：{note}")
    if details:
        detail_text = "｜".join(details)
        for line in _wrap_text(detail_text, regular_font, 7.2, width):
            styled.append((line, regular_font, 7.2, 9))
    return styled


def _build_rows(
    items: Sequence[Mapping[str, Any] | Any],
    work_units: Sequence[Mapping[str, Any] | Any],
    regular_font: str,
    bold_font: str,
) -> list[dict[str, Any]]:
    work_unit_names = {
        int(_value(unit, "id", 0)): str(_value(unit, "name", "")).strip()
        for unit in work_units
        if _value(unit, "id", None) is not None
    }
    description_width = 132
    rows: list[dict[str, Any]] = []

    for index, item in enumerate(items, start=1):
        work_unit_id = _value(item, "work_unit_id", None)
        work_unit_name = work_unit_names.get(int(work_unit_id), "") if work_unit_id else ""
        description = _description_lines(item, work_unit_name, regular_font, bold_font, description_width)
        if not description:
            description = [("未命名品項", bold_font, 8.8, 11)]

        # Extremely long notes are continued in a second table row instead of
        # being clipped or crossing a page boundary.
        first_chunk = description[:14]
        remaining = description[14:]
        chunks = [first_chunk]
        while remaining:
            chunks.append([(f"（續）{_value(item, 'product_name', '')}", bold_font, 8.0, 10)] + remaining[:13])
            remaining = remaining[13:]

        for chunk_index, chunk in enumerate(chunks):
            first = chunk_index == 0
            text_height = sum(line[3] for line in chunk)
            row = {
                "number": str(index) if first else "",
                "description": chunk,
                "size": str(_value(item, "size", "")).strip() if first else "",
                "unit_price": _format_money(_value(item, "unit_price", 0)) if first else "",
                "quantity": (
                    f"{_format_quantity(_value(item, 'quantity', 0))} {str(_value(item, 'unit', '')).strip()}".strip()
                    if first
                    else ""
                ),
                "amount": _format_money(_value(item, "subtotal", 0)) if first else "",
                "height": max(25, text_height + 10),
            }
            rows.append(row)
    return rows


def _paginate_rows(rows: Sequence[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    if not rows:
        return [[]]

    regular_capacity = TABLE_BODY_TOP - INTERMEDIATE_BOTTOM
    final_capacity = TABLE_BODY_TOP - FINAL_ITEMS_BOTTOM
    heights = [float(row["height"]) for row in rows]
    prefix = [0.0]
    for height in heights:
        prefix.append(prefix[-1] + height)

    def segment_height(start: int, end: int) -> float:
        return prefix[end] - prefix[start]

    for page_count in range(1, len(rows) + 1):
        capacities = [regular_capacity] * (page_count - 1) + [final_capacity]
        states: dict[int, tuple[float, list[int]]] = {0: (0.0, [])}
        for page_index, capacity in enumerate(capacities):
            next_states: dict[int, tuple[float, list[int]]] = {}
            pages_left = page_count - page_index - 1
            for start, (cost, breaks) in states.items():
                min_end = start + 1
                max_end = len(rows) - pages_left
                for end in range(min_end, max_end + 1):
                    used = segment_height(start, end)
                    if used > capacity + 0.001:
                        break
                    unused_ratio = (capacity - used) / capacity
                    candidate = (cost + unused_ratio * unused_ratio, breaks + [end])
                    current = next_states.get(end)
                    if current is None or candidate[0] < current[0]:
                        next_states[end] = candidate
            states = next_states
            if not states:
                break
        if len(rows) in states:
            breaks = states[len(rows)][1]
            pages: list[list[dict[str, Any]]] = []
            start = 0
            for end in breaks:
                pages.append(list(rows[start:end]))
                start = end
            return pages

    raise ValueError("PDF item rows cannot be paginated")


def _draw_sidebar(
    pdf: canvas.Canvas,
    record: Mapping[str, Any] | Any,
    regular_font: str,
    bold_font: str,
) -> None:
    pdf.setFillColor(SIDEBAR)
    pdf.rect(0, 0, SIDEBAR_WIDTH, PAGE_HEIGHT, fill=1, stroke=0)

    if LOGO_PATH.exists():
        logo_width = 112
        logo_height = logo_width * 1303 / 1600
        pdf.drawImage(
            ImageReader(str(LOGO_PATH)),
            (SIDEBAR_WIDTH - logo_width) / 2,
            PAGE_HEIGHT - 48 - logo_height,
            width=logo_width,
            height=logo_height,
            preserveAspectRatio=True,
            mask="auto",
        )

    left = 30
    right = SIDEBAR_WIDTH - 10
    pdf.setStrokeColor(INK)
    pdf.setLineWidth(0.7)
    pdf.line(left, 625, right, 625)

    pdf.setFillColor(INK)
    pdf.setFont(regular_font, 9)
    pdf.drawString(left, 610, "顧客")
    y = _draw_wrapped(
        pdf,
        _value(record, "customer_name", "—"),
        left,
        578,
        right - left,
        font_name=bold_font,
        font_size=10.5,
        line_height=14,
        max_lines=4,
    )

    contact = display_customer_contact(
        _value(record, "customer_name", ""),
        _value(record, "contact_person", ""),
    )
    phone = str(_value(record, "phone", "")).strip()
    tax_id = str(_value(record, "tax_id", "")).strip()
    contact_lines = []
    if contact:
        contact_lines.append(contact)
    if phone:
        contact_lines.append(f"TEL：{phone}")
    if tax_id:
        contact_lines.append(f"統編：{tax_id}")
    if contact_lines:
        _draw_wrapped(
            pdf,
            "\n".join(contact_lines),
            left,
            min(y - 18, 492),
            right - left,
            font_name=bold_font,
            font_size=8.8,
            line_height=14,
            max_lines=6,
        )

    pdf.line(left, 91, right, 91)
    pdf.setFont(regular_font, 8.8)
    pdf.drawString(left, 77, "廣達數位印刷")
    pdf.setFont(bold_font, 7.8)
    pdf.drawString(left, 61, "嘉義市西區興達路200號")
    pdf.drawString(left, 45, "TEL：05-2326-333")


def _draw_document_header(
    pdf: canvas.Canvas,
    document_type: str,
    record: Mapping[str, Any] | Any,
    total: float,
    paid_total: float,
    regular_font: str,
    bold_font: str,
) -> None:
    is_order = document_type == "order"
    title = "訂單" if is_order else "報價單"
    number_label = "訂單編號" if is_order else "報價編號"
    number_key = "order_number" if is_order else "quote_number"

    pdf.setFillColor(INK)
    pdf.setFont(bold_font, 24)
    pdf.drawRightString(MAIN_RIGHT, 772, title)

    label_x = MAIN_X + 2
    value_x = MAIN_X + 76
    pdf.setFont(regular_font, 8.8)
    pdf.drawString(label_x, 744, number_label)
    pdf.setFont(regular_font, 11)
    pdf.drawString(value_x, 744, str(_value(record, number_key, "—")))

    pdf.setFont(regular_font, 8.8)
    pdf.drawString(label_x, 718, "日期")
    pdf.setFont(regular_font, 10.5)
    pdf.drawString(value_x, 718, _format_date(_value(record, "created_at", ""), stored_utc=True))

    delivery = _format_date(_value(record, "delivery_date", ""))
    if delivery != "—":
        pdf.setFillColor(MUTED)
        pdf.setFont(regular_font, 8.3)
        pdf.drawRightString(MAIN_RIGHT, 718, f"交貨：{delivery}")

    if is_order:
        status = str(_value(record, "status", "")).strip()
        if status:
            pdf.setFillColor(MUTED)
            pdf.setFont(regular_font, 8.3)
            pdf.drawRightString(MAIN_RIGHT, 700, f"狀態：{status}")

    pdf.setFillColor(INK)
    pdf.setFont(regular_font, 9)
    pdf.drawString(label_x, 690, "應付")
    amount_text = f"{_format_money(total)} NTD"
    pdf.setFont(bold_font, 17)
    pdf.drawString(label_x, 665, amount_text)

    if is_order and paid_total > 0:
        amount_width = pdfmetrics.stringWidth(amount_text, bold_font, 17)
        paid_x = min(label_x + amount_width + 18, MAIN_RIGHT - 105)
        pdf.setFillColor(MUTED)
        pdf.setFont(regular_font, 7.5)
        pdf.drawString(paid_x, 678, "已收款")
        pdf.setFont(bold_font, 9)
        pdf.drawString(paid_x, 663, f"{_format_money(paid_total)} NTD")


def _draw_table_header(pdf: canvas.Canvas, regular_font: str, bold_font: str) -> list[float]:
    widths = [26, 143, 62, 50, 47, MAIN_WIDTH - 328]
    positions = [MAIN_X]
    for width in widths:
        positions.append(positions[-1] + width)

    pdf.setStrokeColor(INK)
    pdf.setLineWidth(1.2)
    pdf.rect(MAIN_X, TABLE_TOP - TABLE_HEADER_HEIGHT, MAIN_WIDTH, TABLE_HEADER_HEIGHT, fill=0, stroke=1)
    pdf.setFillColor(INK)
    pdf.setFont(bold_font, 8.5)
    baseline = TABLE_TOP - 15
    pdf.drawCentredString((positions[0] + positions[1]) / 2, baseline, "序")
    pdf.drawString(positions[1] + 5, baseline, "商品敘述")
    pdf.drawCentredString((positions[2] + positions[3]) / 2, baseline, "規格")
    pdf.drawRightString(positions[4] - 4, baseline, "單價")
    pdf.drawCentredString((positions[4] + positions[5]) / 2, baseline, "數量")
    pdf.drawRightString(positions[6] - 4, baseline, "金額")
    return positions


def _draw_item_rows(
    pdf: canvas.Canvas,
    rows: Sequence[dict[str, Any]],
    positions: Sequence[float],
    regular_font: str,
) -> None:
    y = TABLE_BODY_TOP
    for row in rows:
        height = float(row["height"])
        bottom = y - height
        pdf.setStrokeColor(LIGHT_LINE)
        pdf.setLineWidth(0.35)
        pdf.line(MAIN_X, bottom, MAIN_RIGHT, bottom)

        baseline = y - 16
        pdf.setFillColor(INK)
        pdf.setFont(regular_font, 8.2)
        pdf.drawCentredString((positions[0] + positions[1]) / 2, baseline, row["number"])

        text_y = y - 12
        for text, font_name, font_size, line_height in row["description"]:
            pdf.setFont(font_name, font_size)
            pdf.drawString(positions[1] + 5, text_y, text)
            text_y -= line_height

        pdf.setFont(regular_font, 8)
        size_lines = _wrap_text(row["size"], regular_font, 8, positions[3] - positions[2] - 6)
        size_y = y - 16
        for line in size_lines:
            pdf.drawCentredString((positions[2] + positions[3]) / 2, size_y, line)
            size_y -= 10

        pdf.drawRightString(positions[4] - 4, baseline, row["unit_price"])
        pdf.drawCentredString((positions[4] + positions[5]) / 2, baseline, row["quantity"])
        if row["amount"]:
            pdf.drawString(positions[5] + 4, baseline, "NT$")
            pdf.drawRightString(positions[6] - 4, baseline, row["amount"])
        y = bottom

    if not rows:
        pdf.setFillColor(MUTED)
        pdf.setFont(regular_font, 9)
        pdf.drawCentredString((MAIN_X + MAIN_RIGHT) / 2, TABLE_BODY_TOP - 28, "尚無品項")


def _draw_totals(
    pdf: canvas.Canvas,
    subtotal: float,
    tax_amount: float,
    total: float,
    regular_font: str,
    bold_font: str,
) -> None:
    rows: list[tuple[str, float, str]] = [
        ("小計", subtotal, "normal"),
        ("外加稅", tax_amount, "normal"),
        ("總計", total, "boxed"),
    ]

    row_height = 21
    bottom = NOTES_Y + NOTES_HEIGHT + 16
    top = bottom + row_height * len(rows)
    label_x = MAIN_X + MAIN_WIDTH * 0.55
    currency_x = MAIN_X + MAIN_WIDTH * 0.80

    pdf.setStrokeColor(INK)
    pdf.setLineWidth(0.7)
    pdf.line(MAIN_X, top + 5, MAIN_RIGHT, top + 5)

    y = top - row_height
    for label, amount, style in rows:
        if style == "highlight":
            pdf.setFillColor(SOFT_FILL)
            pdf.rect(label_x - 10, y - 3, MAIN_RIGHT - label_x + 10, row_height, fill=1, stroke=0)
        if style in {"boxed", "highlight"}:
            pdf.setStrokeColor(INK)
            pdf.setLineWidth(1)
            pdf.rect(label_x - 10, y - 3, MAIN_RIGHT - label_x + 10, row_height, fill=0, stroke=1)

        pdf.setFillColor(INK)
        pdf.setFont(bold_font if style in {"boxed", "highlight"} else regular_font, 9)
        pdf.drawString(label_x, y + 3, label)
        pdf.setFont(regular_font, 8.5)
        pdf.drawString(currency_x, y + 3, "NT$")
        pdf.setFont(bold_font if style == "highlight" else regular_font, 9)
        pdf.drawRightString(MAIN_RIGHT - 4, y + 3, _format_money(amount))
        y -= row_height


def _draw_notes(pdf: canvas.Canvas, regular_font: str) -> None:
    pdf.setFillColor(INK)
    pdf.setFont(regular_font, 8.5)
    pdf.drawString(MAIN_X + 24, NOTES_Y + NOTES_HEIGHT + 5, "備註")
    pdf.setStrokeColor(INK)
    pdf.setLineWidth(0.7)
    pdf.rect(MAIN_X, NOTES_Y, MAIN_WIDTH, NOTES_HEIGHT, fill=0, stroke=1)

    y = NOTES_Y + NOTES_HEIGHT - 14
    for note in FIXED_NOTES:
        pdf.setFillColor(INK)
        pdf.setFont(regular_font, 7.2)
        pdf.drawString(MAIN_X + 8, y, "※")
        lines = _wrap_text(note, regular_font, 7.2, MAIN_WIDTH - 38)
        for line in lines:
            pdf.drawString(MAIN_X + 27, y, line)
            y -= 9
        y -= 3


def _draw_page_number(pdf: canvas.Canvas, page_number: int, page_count: int, regular_font: str) -> None:
    pdf.setFillColor(MUTED)
    pdf.setFont(regular_font, 7.5)
    pdf.drawRightString(MAIN_RIGHT, 14, f"第 {page_number} / {page_count} 頁")


def build_document_pdf(
    document_type: str,
    record: Mapping[str, Any] | Any,
    items: Sequence[Mapping[str, Any] | Any],
    work_units: Sequence[Mapping[str, Any] | Any],
    *,
    subtotal: float,
    tax_amount: float,
    total: float,
    paid_total: float = 0,
    balance: float = 0,
) -> bytes:
    if document_type not in {"quote", "order"}:
        raise ValueError("document_type must be quote or order")

    regular_font, bold_font = _register_fonts()
    rows = _build_rows(items, work_units, regular_font, bold_font)
    pages = _paginate_rows(rows)

    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=A4, pageCompression=1)
    document_number = _value(record, "order_number" if document_type == "order" else "quote_number", "")
    pdf.setTitle(f"{'訂單' if document_type == 'order' else '報價單'} {document_number}")
    pdf.setAuthor("廣達數位印刷")
    pdf.setSubject("PrintShop 匯出文件")

    page_count = len(pages)
    for page_number, page_rows in enumerate(pages, start=1):
        _draw_sidebar(pdf, record, regular_font, bold_font)
        _draw_document_header(pdf, document_type, record, total, paid_total, regular_font, bold_font)
        positions = _draw_table_header(pdf, regular_font, bold_font)
        _draw_item_rows(pdf, page_rows, positions, regular_font)

        if page_number == page_count:
            _draw_totals(
                pdf,
                subtotal,
                tax_amount,
                total,
                regular_font,
                bold_font,
            )
            _draw_notes(pdf, regular_font)
        else:
            pdf.setFillColor(MUTED)
            pdf.setFont(regular_font, 7.5)
            pdf.drawRightString(MAIN_RIGHT, INTERMEDIATE_BOTTOM - 14, "品項續下頁")

        _draw_page_number(pdf, page_number, page_count, regular_font)
        pdf.showPage()

    pdf.save()
    return output.getvalue()
