"""Reproducible seed: `python -m app.seed` (drops and recreates all demo data)."""
from decimal import Decimal as D
from .models import *


def seed(sf, reset=True):
    db = sf()
    if reset:
        for t in reversed(Base.metadata.sorted_tables): db.execute(t.delete())
    db.add(Customer(id=1, name="ABC Construction", city="Bellevue", state="WA", max_discount_pct=D("15")))
    wh = Warehouse(id=1, name="Bellevue Distribution Center", region="WA"); db.add(wh)
    db.add(DeliveryRule(warehouse_id=1, region="WA", lead_days=1))
    # sku, name, keywords, on_hand, reserved, price, supplier lead time (None => unknown)
    rows = [
        ("CEM-50KG", "Cement, 50 kg bag", "cement", 800, 0, "8.50"),
        ("DRY-4X8", "Drywall, 4 x 8 ft sheet", "drywall,sheetrock", 300, 0, "14.00"),
        ("STL-10FT", "Steel stud, 10 ft", "steel stud,metal stud,stud", 75, 0, "6.50"),
        ("PLY-4X8", "Plywood, 4 x 8 ft", "plywood", 250, 20, "32.00"),
        ("LUM-2X4X8", "Lumber, 2 x 4 x 8 ft", "lumber,2x4,wood stud,stud", 500, 50, "5.25"),
        ("INS-STD", "Insulation", "insulation", 180, 10, "24.00"),
        ("STL-10FT-20G", "Steel stud, 10 ft, 20 gauge (heavy)", "20ga steel stud,heavy gauge stud", 40, 0, "6.90"),
    ]
    book = PriceBook(id=1, name="STANDARD-2026"); db.add(book)
    for i, (sku, name, kw, oh, rs, price) in enumerate(rows, 1):
        db.add(Product(id=i, sku=sku, name=name, keywords=kw))
        db.add(Inventory(product_id=i, warehouse_id=1, on_hand=oh, reserved=rs))
        db.add(PriceBookEntry(price_book_id=1, product_id=i, price=D(price)))
        # Supplier lead times intentionally unknown: the system must never promise supplier dates.
        db.add(Supplier(product_id=i, name=f"Supplier for {sku}", lead_time_days=None))
    # Approved: heavy-gauge stud may replace standard stud. NOT approved: lumber for steel stud.
    db.add(Compatibility(product_id=3, substitute_id=7, approved=True, note="Engineering-approved heavy gauge equivalent"))
    db.add(Compatibility(product_id=3, substitute_id=5, approved=False, note="Wood is NOT a structural substitute for steel"))
    db.commit(); db.close()


if __name__ == "__main__":
    import os
    seed(make_session_factory(os.getenv("BUILDMATE_DB", "sqlite:///./buildmate.db")))
    print("Seeded.")
