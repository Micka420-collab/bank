"""Remplit une base de DÉMONSTRATION avec 4 mois d'opérations réalistes (salaire, loyer, CAF, courses…).

Usage (jamais sur ta vraie base) :
    BANK_ENV=dev BANK_DATABASE_URL=sqlite:///./data/demo.db BANK_MASTER_KEY=... python -m scripts.demo_seed
"""

from __future__ import annotations

import json
import random
from datetime import date, timedelta

from app.config import Settings
from app.db import Base, make_engine, make_sessionmaker, utcnow
from app.domain.recurring import add_months
from app.importers.base import ParsedTransaction
from app.models import Account, AccountKind, Category, HouseholdProfile, Provider, User
from app.security.auth import hash_password
from app.security.crypto import Keyring, install_keyring
from app.services.categories import seed_categories
from app.services.ingest import ingest

DEMO_USER, DEMO_PASSWORD = "micka", "demo demo demo 42"


def monthly(day: int, label: str, cents: int, months: int, today: date, jitter: int = 0) -> list[ParsedTransaction]:
    out = []
    for i in range(months, -1, -1):
        d = add_months(today.replace(day=1), -i, day)
        if d <= today:
            amount = cents + (random.randint(-jitter, jitter) if jitter else 0)  # noqa: S311
            out.append(ParsedTransaction(d, amount, label))
    return out


def main() -> None:
    random.seed(42)
    settings = Settings()
    install_keyring(Keyring(settings.master_keys))
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    today = date.today()  # noqa: DTZ011
    with make_sessionmaker(engine)() as db:
        seed_categories(db)
        if db.query(User).count() == 0:
            db.add(User(username=DEMO_USER, password_hash=hash_password(DEMO_PASSWORD)))
        courant = Account(
            name="Compte courant",
            kind=AccountKind.CHECKING,
            institution="Crédit Agricole",
            provider=Provider.FILE,
            balance_cents=64_000,
            balance_at=utcnow(),
        )
        livret = Account(
            name="Livret A",
            kind=AccountKind.LIVRET_A,
            provider=Provider.MANUAL,
            balance_cents=480_000,
            balance_at=utcnow(),
            include_in_budget=False,
        )
        lep = Account(
            name="LEP",
            kind=AccountKind.LEP,
            provider=Provider.MANUAL,
            balance_cents=310_000,
            balance_at=utcnow(),
            include_in_budget=False,
        )
        db.add_all([courant, livret, lep])
        db.flush()

        txns: list[ParsedTransaction] = []
        txns += monthly(28, "VIR SEPA RECU /DE BRGM /MOTIF SALAIRE", 215_000, 4, today)
        txns += monthly(3, "PRLV SEPA FONCIA LOYER ECH/03 ID EMETTEUR/FR12ZZZ", -65_000, 4, today)
        txns += monthly(5, "VIR CAF DU LOIRET PRIME ACTIVITE", 25_829, 4, today)
        txns += monthly(5, "VIR CAF DU LOIRET APL", 3_188, 4, today)
        txns += monthly(10, "PRLV SEPA MAIF ASSURANCE HABITATION", -3_800, 4, today)
        txns += monthly(12, "PRLV SEPA FREE MOBILE", -1_999, 4, today)
        txns += monthly(16, "PRLV SEPA EDF CLIENTS PARTICULIERS", -6_200, 4, today, jitter=1_500)
        txns += monthly(25, "PRLV SEPA NETFLIX.COM", -1_349, 4, today)
        txns += monthly(30, "VIR VERS LIVRET A", -30_000, 4, today)
        shops = [
            ("CB CARREFOUR MARKET {d} ORLEANS", 3_000, 9_000),
            ("CB LECLERC {d} SARAN", 4_000, 12_000),
            ("CB BOULANGERIE DU MARTROI {d}", 300, 900),
            ("CB TOTALENERGIES {d} ORLEANS", 4_000, 7_000),
            ("CB PHARMACIE DU CENTRE {d}", 500, 2_500),
            ("CB UBER *EATS {d}", 1_500, 3_000),
            ("CB PIZZERIA DA MARIO {d}", 2_000, 4_500),
            ("CB AMAZON PAYMENTS {d}", 1_000, 6_000),
            ("CB CINEMA PATHE {d} ORLEANS", 1_100, 2_400),
        ]
        d = add_months(today.replace(day=1), -4)
        while d <= today:
            for _ in range(random.choice([0, 1, 1, 2])):  # noqa: S311
                label, lo, hi = random.choice(shops)  # noqa: S311
                txns.append(ParsedTransaction(d, -random.randint(lo, hi), label.format(d=d.strftime("%d/%m"))))  # noqa: S311
            d += timedelta(days=1)
        txns.append(ParsedTransaction(today - timedelta(days=40), 31_200, "VIR DGFIP REMBOURSEMENT IMPOT"))
        ingest(db, courant, sorted(txns, key=lambda t: t.booking_date), provider="file")

        for slug, budget in (("courses", 42_000), ("restaurants", 12_000), ("loisirs", 6_000)):
            cat = db.query(Category).filter_by(slug=slug).one()
            cat.monthly_budget_cents = budget
        db.merge(
            HouseholdProfile(
                id=1,
                data=json.dumps(
                    {
                        "birth_date": "1991-04-12",
                        "postal_insee_code": "45234",
                        "housing": "locataire_vide",
                        "rent_cents": 65_000,
                        "activity": "actif",
                        "couple": False,
                        "partner_birth_date": None,
                        "partner_monthly_net_cents": 0,
                        "children_birth_dates": [],
                        "annual_taxable_income_n2_cents": None,
                    }
                ),
            )
        )
        db.commit()
    print(f"Base de démo prête : {DEMO_USER} / {DEMO_PASSWORD}")


if __name__ == "__main__":
    main()
