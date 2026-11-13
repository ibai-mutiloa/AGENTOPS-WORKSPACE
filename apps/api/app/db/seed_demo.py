"""Load idempotent synthetic demo data. Requires DEMO_PASSWORD in the local environment."""

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import hash_password
from app.core.config import get_settings
from app.db.models import (
    Document,
    Membership,
    Order,
    Organization,
    Product,
    ProductCompatibility,
    User,
)
from app.db.session import create_db_engine

FIXTURE_PATH = Path(__file__).resolve().parents[4] / "fixtures" / "demo.json"


def seed(session: Session, password: str) -> dict[str, str]:
    if len(password) < 12:
        raise ValueError("Demo password must have at least 12 characters")
    fixture: dict[str, Any] = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    result: dict[str, str] = {}
    for org_data in fixture["organizations"]:
        organization = session.scalar(
            select(Organization).where(Organization.name == org_data["name"])
        )
        if organization is None:
            organization = Organization(name=org_data["name"])
            session.add(organization)
            session.flush()
        result[organization.name] = str(organization.id)
        for user_data in org_data["users"]:
            email = user_data["email"].lower()
            user = session.scalar(select(User).where(User.email == email))
            if user is None:
                user = User(
                    external_subject=f"local:{email}",
                    email=email,
                    password_hash=hash_password(password),
                )
                session.add(user)
                session.flush()
            membership = session.scalar(
                select(Membership).where(
                    Membership.organization_id == organization.id, Membership.user_id == user.id
                )
            )
            if membership is None:
                session.add(
                    Membership(
                        organization_id=organization.id, user_id=user.id, role=user_data["role"]
                    )
                )
        for product_data in org_data["products"]:
            product = session.scalar(
                select(Product).where(
                    Product.organization_id == organization.id, Product.sku == product_data["sku"]
                )
            )
            if product is None:
                product = Product(
                    organization_id=organization.id,
                    sku=product_data["sku"],
                    name=product_data["name"],
                    specifications_json=json.dumps(product_data["specifications"], sort_keys=True),
                    available_units=product_data["available_units"],
                )
                session.add(product)
                session.flush()
            for rule in product_data["compatibility"]:
                existing = session.scalar(
                    select(ProductCompatibility).where(
                        ProductCompatibility.organization_id == organization.id,
                        ProductCompatibility.product_id == product.id,
                        ProductCompatibility.assembly_id == rule["assembly_id"],
                    )
                )
                if existing is None:
                    session.add(
                        ProductCompatibility(
                            organization_id=organization.id,
                            product_id=product.id,
                            assembly_id=rule["assembly_id"],
                            compatible=rule["compatible"],
                            rationale=rule["rationale"],
                        )
                    )
            for doc_data in product_data["documents"]:
                existing_doc = session.scalar(
                    select(Document).where(
                        Document.organization_id == organization.id,
                        Document.source_ref == doc_data["source_ref"],
                    )
                )
                if existing_doc is None:
                    session.add(
                        Document(
                            organization_id=organization.id,
                            product_id=product.id,
                            source_ref=doc_data["source_ref"],
                            title=doc_data["title"],
                            version=doc_data["version"],
                            active=doc_data["active"],
                            content=doc_data["content"],
                        )
                    )
        for order_data in org_data["orders"]:
            existing_order = session.scalar(
                select(Order).where(
                    Order.organization_id == organization.id,
                    Order.order_number == order_data["order_number"],
                )
            )
            if existing_order is None:
                session.add(Order(organization_id=organization.id, **order_data))
    session.commit()
    return result


def main() -> None:
    settings = get_settings()
    if settings.demo_password is None:
        raise SystemExit("Set DEMO_PASSWORD in the local .env before seeding")
    with Session(create_db_engine()) as session:
        organizations = seed(session, settings.demo_password.get_secret_value())
    for name, organization_id in organizations.items():
        print(f"{name}: {organization_id}")


if __name__ == "__main__":
    main()
