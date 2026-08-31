"""Remove secrets and deterministically pseudonymize a cloned staging DB."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import re

from sqlalchemy import select

from app.core.database import async_session_maker
from app.models import BankConnection, Payee, PayeeTaxId, Transaction, User


KEY = os.environ.get("STAGING_PSEUDONYM_KEY", "local-staging-only-change-me").encode()


def pseudonym(value: str | None, label: str) -> str:
    digest = hmac.new(KEY, f"{label}:{value or ''}".encode(), hashlib.sha256).hexdigest()[:12]
    return f"{label}-{digest}"


def mask_identifiers(value: str | None) -> str | None:
    if not value:
        return value
    return re.sub(r"\b\d{11,14}\b", lambda match: f"id-{hashlib.sha256(match.group().encode()).hexdigest()[:8]}", value)


async def main():
    async with async_session_maker() as session:
        for user in (await session.execute(select(User))).scalars().all():
            user.email = f"{pseudonym(user.email, 'user')}@example.invalid"
            user.hashed_password = "!staging-auth-disabled!"
            user.totp_secret = None
        for connection in (await session.execute(select(BankConnection))).scalars().all():
            connection.credentials = None
            connection.external_id = pseudonym(connection.external_id, "connection")
        for payee in (await session.execute(select(Payee))).scalars().all():
            payee.name = pseudonym(payee.name, "payee")
            payee.email = None
            payee.phone = None
            payee.address = None
            payee.website = None
        for tax_id in (await session.execute(select(PayeeTaxId))).scalars().all():
            tax_id.value = None
            tax_id.fingerprint = None
            tax_id.last4 = None
        for tx in (await session.execute(select(Transaction))).scalars().all():
            tx.payee = mask_identifiers(tx.payee)
            tx.notes = mask_identifiers(tx.notes)
        await session.commit()
        print({"status": "pseudonymized", "secrets_removed": True})


if __name__ == "__main__":
    asyncio.run(main())
