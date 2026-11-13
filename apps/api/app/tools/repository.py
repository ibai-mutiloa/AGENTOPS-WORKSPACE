import json
import uuid
from collections.abc import Callable
from typing import Any, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditEvent, Document, Order, Product, ProductCompatibility, ToolInvocation

T = TypeVar("T")


class ToolFailure(Exception):
    def __init__(self, name: str):
        self.name = name
        super().__init__(f"{name} failed")


class BusinessTools:
    """Every query is scoped to a trusted organization from authentication."""

    def __init__(self, session: Session, organization_id: uuid.UUID, request_id: uuid.UUID):
        self.session = session
        self.organization_id = organization_id
        self.request_id = request_id

    def _run(self, name: str, arguments: dict[str, str], operation: Callable[[], T]) -> T:
        invocation = ToolInvocation(
            organization_id=self.organization_id,
            request_id=self.request_id,
            tool_name=name,
            input_json=json.dumps(arguments, sort_keys=True),
        )
        self.session.add(invocation)
        try:
            result = operation()
        except Exception as exc:
            invocation.error = type(exc).__name__
            raise ToolFailure(name) from exc
        invocation.result_json = json.dumps(
            {
                "found": result is not None,
                "count": len(result) if isinstance(result, list) else None,
            }
        )
        self.session.add(
            AuditEvent(
                organization_id=self.organization_id,
                request_id=self.request_id,
                event_type="tool.invoked",
                details_json=json.dumps({"tool": name}),
            )
        )
        return result

    def get_product_details(self, product_id: str) -> dict[str, Any] | None:
        def lookup() -> dict[str, Any] | None:
            product = self.session.scalar(
                select(Product).where(
                    Product.organization_id == self.organization_id, Product.sku == product_id
                )
            )
            if product is None:
                return None
            return {"sku": product.sku, "name": product.name}

        return self._run("get_product_details", {"product_id": product_id}, lookup)

    def get_product_specifications(self, product_id: str) -> dict[str, Any] | None:
        def lookup() -> dict[str, Any] | None:
            product = self.session.scalar(
                select(Product).where(
                    Product.organization_id == self.organization_id, Product.sku == product_id
                )
            )
            return json.loads(product.specifications_json) if product else None

        return self._run("get_product_specifications", {"product_id": product_id}, lookup)

    def check_product_compatibility(
        self, product_id: str, assembly_id: str
    ) -> dict[str, Any] | None:
        def lookup() -> dict[str, Any] | None:
            rule = self.session.scalar(
                select(ProductCompatibility)
                .join(
                    Product,
                    (Product.id == ProductCompatibility.product_id)
                    & (Product.organization_id == ProductCompatibility.organization_id),
                )
                .where(
                    ProductCompatibility.organization_id == self.organization_id,
                    Product.sku == product_id,
                    ProductCompatibility.assembly_id == assembly_id,
                )
            )
            if rule is None:
                return None
            return {"compatible": rule.compatible, "rationale": rule.rationale}

        return self._run(
            "check_product_compatibility",
            {"product_id": product_id, "assembly_id": assembly_id},
            lookup,
        )

    def get_product_availability(self, product_id: str) -> int | None:
        def lookup() -> int | None:
            product = self.session.scalar(
                select(Product).where(
                    Product.organization_id == self.organization_id, Product.sku == product_id
                )
            )
            return product.available_units if product else None

        return self._run("get_product_availability", {"product_id": product_id}, lookup)

    def get_order_status(self, order_id: str) -> str | None:
        def lookup() -> str | None:
            order = self.session.scalar(
                select(Order).where(
                    Order.organization_id == self.organization_id, Order.order_number == order_id
                )
            )
            return order.status if order else None

        return self._run("get_order_status", {"order_id": order_id}, lookup)

    def search_documents(self, product_id: str) -> list[dict[str, str]]:
        def lookup() -> list[dict[str, str]]:
            documents = self.session.scalars(
                select(Document)
                .join(
                    Product,
                    (Product.id == Document.product_id)
                    & (Product.organization_id == Document.organization_id),
                )
                .where(
                    Document.organization_id == self.organization_id,
                    Product.sku == product_id,
                    Document.active.is_(True),
                )
                .order_by(Document.source_ref)
            ).all()
            return [
                {
                    "source_ref": doc.source_ref,
                    "title": doc.title,
                    "version": doc.version,
                    "content": doc.content,
                }
                for doc in documents
            ]

        return self._run("search_documents", {"product_id": product_id}, lookup)
