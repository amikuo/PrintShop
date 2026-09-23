"""Deterministic, decimal-based pre-tax quotation calibration."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json

MODE = "calibrated"


def number(value, label):
    try:
        value = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"{label}必須是有效數字")
    if not value.is_finite() or value < 0 or value > Decimal("1000000000"):
        raise ValueError(f"{label}必須介於 0 與 10 億之間")
    return value


def whole(value):
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def flatten(payload):
    if payload.get("mode") == "project":
        return [i for u in payload.get("work_units", []) for i in u.get("items", [])
                if str(i.get("product_name") or "").strip()]
    return [i for i in payload.get("items", []) if str(i.get("product_name") or "").strip()]


def calibrate(payload):
    if payload.get("mode") == "project":
        for unit in payload.get("work_units", []):
            if any(i.get("product_name", "").strip() for i in unit.get("items", [])) and not (unit.get("name") or "").strip():
                raise ValueError("請先填寫每個工作單位的名稱")
    items = flatten(payload)
    if not items:
        raise ValueError("請先填寫至少一個品項")
    rows = []
    gross = Decimal(0)
    for item in items:
        qty = number(item.get("quantity", 0), "數量")
        raw_variants = item.get("variant_count", 1)
        variants = number(1 if raw_variants in (None, "") else raw_variants, "款數")
        if variants < 1 or variants != variants.to_integral_value():
            raise ValueError("款數必須是大於或等於 1 的整數")
        count = qty * variants
        if count <= 0 or count > Decimal("1000000000"):
            raise ValueError("校準品項的數量乘款數必須大於 0 且不超過 10 億")
        price = number(item.get("unit_price", 0), "約定含稅單價")
        gross += price * count
        base_price = (price / Decimal("1.05")).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        rows.append({"product_name": item["product_name"], "quantity": str(qty),
                     "variant_count": int(variants), "unit": item.get("unit", ""),
                     "source_price": str(price), "base_price": str(base_price),
                     "before": whole(base_price * count), "_count": count})
    requested = payload.get("target_total")
    target = number(requested if requested not in (None, "") else whole(gross), "含稅總額")
    if target != target.to_integral_value():
        raise ValueError("指定含稅總額請填整元")
    target = int(target)
    guess = whole(Decimal(target) / Decimal("1.05"))
    bases = [b for b in range(max(0, guess - 2), guess + 3)
             if b + whole(Decimal(b) * Decimal("0.05")) == target]
    if not bases:
        raise ValueError("此總額無法同時符合未稅合計整元及 5% 稅額四捨五入，請調整目標總額 1 元後重算")
    base = bases[0]
    raw_index = payload.get("adjustment_index")
    try:
        index = int(raw_index) if raw_index not in (None, "") else len(rows) - 1
    except (ValueError, TypeError):
        raise ValueError("請重新選擇尾差調整品項")
    if index < 0 or index >= len(rows):
        raise ValueError("尾差調整品項已不存在，請重新選擇")
    delta = base - sum(r["before"] for r in rows)
    for n, row in enumerate(rows):
        count = row.pop("_count")
        amount = row["before"] + (delta if n == index else 0)
        if amount < 0:
            raise ValueError("調整後品項金額不可為負數，請選擇其他品項或修正總額")
        # Minimize displayed precision for EVERY row, not only the adjusted row.
        # Prefer the original agreed price where its rounded product matches.
        reference = Decimal(row["source_price"]) / Decimal("1.05")
        for places in range(2, 11):
            step = Decimal(1).scaleb(-places)
            candidates = [reference.quantize(step, rounding=ROUND_HALF_UP),
                          (Decimal(amount) / count).quantize(step, rounding=ROUND_HALF_UP)]
            for price in candidates:
                if whole(price * count) == amount:
                    break
            else:
                continue
            break
        else:
            raise ValueError("數量過大，無法以可顯示的單價精度校準")
        row.update(unit_price=format(price, "f").rstrip("0").rstrip(".") if "." in format(price, "f") else str(price),
                   subtotal=amount, adjustment=amount - row["before"])
    result = {"items": rows, "source_total": str(gross), "total": target,
              "subtotal": base, "tax_amount": target - base, "adjustment": delta,
              "adjustment_index": index}
    result["signature"] = hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return result


def prepare(payload):
    clean = deepcopy(payload)
    for item in flatten(clean):
        item.pop("_calibrated_subtotal", None)
    if clean.get("tax_mode") != MODE:
        return clean, ""
    result = calibrate(clean)
    if clean.get("calibration_signature") != result["signature"]:
        raise ValueError("請先重新計算校準明細並確認金額，再儲存")
    source = deepcopy({k: clean.get(k) for k in ("items", "work_units", "target_total", "adjustment_index")})
    for item, row in zip(flatten(clean), result["items"]):
        item["unit_price"] = row["unit_price"]
        item["_calibrated_subtotal"] = row["subtotal"]
    return clean, json.dumps({"source": source, "result": result}, ensure_ascii=False)


def restore_source(payload, record):
    if record["tax_mode"] == MODE and record["calibration_json"]:
        saved = json.loads(record["calibration_json"])
        payload.update({k: v for k, v in saved["source"].items() if v is not None})
    return payload
