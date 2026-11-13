import re
from typing import Literal

from pydantic import BaseModel

Category = Literal[
    "documentation", "specification", "compatibility", "availability", "order_status", "unsupported"
]


class RequestAnalysis(BaseModel):
    category: Category
    product_id: str | None = None
    assembly_id: str | None = None
    order_id: str | None = None
    missing_information: list[str]


_reference = re.compile(r"\b[A-Z]{2,5}-\d{2,6}\b", re.IGNORECASE)
_order = re.compile(r"\bORD-\d{3,8}\b", re.IGNORECASE)


def analyze_request(subject: str, body: str) -> RequestAnalysis:
    text = f"{subject}\n{body}"
    lower = text.lower()
    order_match = _order.search(text)
    references = [match.group(0).upper() for match in _reference.finditer(text)]
    references = [
        reference for reference in dict.fromkeys(references) if not reference.startswith("ORD-")
    ]

    if any(word in lower for word in ("order status", "where is my order", "pedido", "tracking")):
        category: Category = "order_status"
    elif any(word in lower for word in ("compatib", "fit with", "works with")):
        category = "compatibility"
    elif any(word in lower for word in ("availab", "stock", "inventory")):
        category = "availability"
    elif any(word in lower for word in ("specification", "specs", "technical data")):
        category = "specification"
    elif any(word in lower for word in ("document", "datasheet", "manual")):
        category = "documentation"
    else:
        category = "unsupported"

    product_id = references[0] if category != "order_status" and references else None
    assembly_id = references[1] if category == "compatibility" and len(references) > 1 else None
    missing: list[str] = []
    if category == "unsupported":
        missing.append("A supported request category is required")
    elif category == "order_status" and order_match is None:
        missing.append("Order reference is required")
    elif category != "order_status" and product_id is None:
        missing.append("Product reference is required")
    if category == "compatibility" and assembly_id is None:
        missing.append("Assembly reference is required")

    return RequestAnalysis(
        category=category,
        product_id=product_id,
        assembly_id=assembly_id,
        order_id=order_match.group(0).upper() if order_match else None,
        missing_information=missing,
    )
