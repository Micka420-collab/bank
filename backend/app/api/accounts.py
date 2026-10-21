"""Comptes : liste, création manuelle, édition."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.api.deps import AuthDep, DbDep
from app.db import utcnow
from app.models import Account, AccountKind, Provider
from app.security import audit
from app.security.crypto import get_keyring, normalize_identifier

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


def iban_is_valid(iban: str) -> bool:
    s = normalize_identifier(iban)
    if not (15 <= len(s) <= 34) or not s[:2].isalpha() or not s[2:4].isdigit() or not s.isalnum():
        return False
    rearranged = s[4:] + s[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(digits) % 97 == 1


def clean_iban(v: str | None) -> str | None:
    if v and not iban_is_valid(v):
        raise ValueError("IBAN invalide (clé de contrôle)")
    return normalize_identifier(v) if v else None


def mask_iban(iban: str | None) -> str | None:
    if not iban:
        return None
    s = normalize_identifier(iban)
    return f"{s[:4]} •••• •••• {s[-4:]}"


class AccountOut(BaseModel):
    id: int
    name: str
    kind: AccountKind
    institution: str | None
    currency: str
    iban_masked: str | None
    provider: Provider
    balance_cents: int
    balance_at: datetime | None
    include_in_budget: bool
    archived: bool
    connection_status: str | None


def to_out(a: Account) -> AccountOut:
    return AccountOut(
        id=a.id,
        name=a.name,
        kind=a.kind,
        institution=a.institution,
        currency=a.currency,
        iban_masked=mask_iban(a.iban),
        provider=a.provider,
        balance_cents=a.balance_cents,
        balance_at=a.balance_at,
        include_in_budget=a.include_in_budget,
        archived=a.archived,
        connection_status=a.connection.status if a.connection else None,
    )


class AccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: AccountKind = AccountKind.CHECKING
    institution: str | None = Field(default=None, max_length=120)
    iban: str | None = Field(default=None, max_length=50)
    balance_cents: int = Field(default=0, ge=-100_000_000_00, le=100_000_000_00)
    include_in_budget: bool | None = None

    @field_validator("iban")
    @classmethod
    def _iban(cls, v: str | None) -> str | None:
        return clean_iban(v)


class AccountPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    kind: AccountKind | None = None
    institution: str | None = Field(default=None, max_length=120)
    iban: str | None = Field(default=None, max_length=50)
    balance_cents: int | None = Field(default=None, ge=-100_000_000_00, le=100_000_000_00)
    include_in_budget: bool | None = None
    archived: bool | None = None

    @field_validator("iban")
    @classmethod
    def _iban(cls, v: str | None) -> str | None:
        return clean_iban(v)


@router.get("")
def list_accounts(_: AuthDep, db: DbDep) -> list[AccountOut]:
    return [to_out(a) for a in db.scalars(select(Account).order_by(Account.archived, Account.kind, Account.name))]


def _set_iban(acc: Account, iban: str | None) -> None:
    acc.iban = iban
    acc.iban_bidx = get_keyring().blind_index(iban, "account.iban") if iban else None


@router.post("", status_code=201)
def create_account(body: AccountIn, ctx: AuthDep, db: DbDep) -> AccountOut:
    acc = Account(
        name=body.name,
        kind=body.kind,
        institution=body.institution,
        provider=Provider.MANUAL,
        balance_cents=body.balance_cents,
        balance_at=utcnow(),
        include_in_budget=body.include_in_budget
        if body.include_in_budget is not None
        else body.kind == AccountKind.CHECKING,
    )
    if body.iban:
        idx = get_keyring().blind_index(body.iban, "account.iban")
        if db.scalar(select(Account.id).where(Account.iban_bidx == idx)):
            raise HTTPException(status.HTTP_409_CONFLICT, "Un compte avec cet IBAN existe déjà")
        _set_iban(acc, body.iban)
    db.add(acc)
    db.flush()
    audit.record(db, ctx.user.username, "account.create", account=acc.id, kind=acc.kind)
    return to_out(acc)


@router.patch("/{account_id}")
def update_account(account_id: int, body: AccountPatch, ctx: AuthDep, db: DbDep) -> AccountOut:
    acc = db.get(Account, account_id)
    if acc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Compte introuvable")
    data = body.model_dump(exclude_unset=True)
    if "balance_cents" in data and acc.provider == Provider.ENABLEBANKING:
        raise HTTPException(status.HTTP_409_CONFLICT, "Le solde d'un compte synchronisé vient de la banque")
    if "iban" in data:
        _set_iban(acc, data.pop("iban"))
    if "balance_cents" in data:
        acc.balance_at = utcnow()
    for key, value in data.items():
        setattr(acc, key, value)
    audit.record(db, ctx.user.username, "account.update", account=acc.id, fields=sorted(body.model_fields_set))
    return to_out(acc)
