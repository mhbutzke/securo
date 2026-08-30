"""Central category ownership rules.

Category provenance is deliberately kept on the transaction row so an
automatic provider/rule pass can never silently replace a user's decision.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.transaction import Transaction

CATEGORY_ORIGINS = {"manual", "rule", "provider", "system", "legacy", None}


def assign_category(
    transaction: "Transaction",
    category_id,
    *,
    origin: str | None,
    rule_id=None,
    force_manual: bool = False,
) -> bool:
    """Assign/clear a category while preserving manual ownership.

    Returns ``True`` when the row changed. ``origin=None`` is the explicit
    manual clear used by the UI to return a transaction to automatic rules.
    """
    if origin not in CATEGORY_ORIGINS:
        raise ValueError(f"Invalid category origin: {origin}")
    current_origin = getattr(transaction, "category_origin", None)
    if origin is None and category_id is None:
        force_manual = True
    if current_origin == "manual" and origin != "manual" and not force_manual:
        return False
    new_rule_id = None if origin == "manual" else rule_id
    changed = (
        transaction.category_id != category_id
        or getattr(transaction, "category_origin", None) != origin
        or getattr(transaction, "category_rule_id", None) != new_rule_id
    )
    transaction.category_id = category_id
    transaction.category_origin = origin
    transaction.category_rule_id = new_rule_id
    return changed


def mark_legacy(transaction: "Transaction") -> None:
    """Mark an existing category as provisional legacy data."""
    if transaction.category_id is not None and getattr(transaction, "category_origin", None) is None:
        transaction.category_origin = "legacy"
