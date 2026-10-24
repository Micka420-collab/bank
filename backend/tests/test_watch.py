"""Veille (scénario A) : une source officielle publie, Bank la relève sans rien envoyer te concernant, date ce
qu'il a lu, détecte les changements, rapproche les thèmes de ta situation — et la lecture du réseau ne peut ni
viser le réseau local, ni lire un XML piégé, ni se laisser déborder par une réponse géante."""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import select
from starlette.testclient import TestClient

from app.db import utcnow
from app.domain import watch as dw
from app.models import FiscalSimulation, WatchItem, WatchSource
from app.security import netfetch
from app.services import watch as ws

ECO = "https://www.economie.gouv.fr/rss/toutesactualites"
PYPI = "https://pypi.org/rss/project/openfisca-france/releases.xml"
SERVICE_PUBLIC = "https://www.service-public.gouv.fr/abonnements/rss/actu-actualites-particuliers.rss"
SP_FEED = (Path(__file__).parent / "fixtures" / "calendrier" / "service_public_actualites.rss").read_bytes()


def rss(*items: tuple[str, str, str, str]) -> bytes:
    """Flux au format du ministère (dates « AAAA-MM-JJ HH:MM:SS », guid non permaliens)."""
    body = "".join(
        f"<item><title>{t}</title><link>{link}</link><description>{d}</description>"
        f'<pubDate>{p}</pubDate><guid isPermaLink="false">{link[-6:]}</guid></item>'
        for t, link, d, p in items
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?><rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<channel><title>Flux RSS Toutes les actualités</title><description/>{body}</channel></rss>"
    ).encode()


LIVRET = (
    " Livret A et LEP : les nouveaux taux au 1er février",
    "https://www.economie.gouv.fr/particuliers/livret-a-taux-000111",
    "Le taux du &lt;b&gt;livret A&lt;/b&gt; évolue.",
    "2026-10-21 15:12:28",
)
FRENCH_TECH = (
    "La French tech : un écosystème avec une ambition mondiale",
    "https://www.economie.gouv.fr/actualites/french-tech-000222",
    "Startups.",
    "2026-10-21 13:42:52",
)
ENGINE = (
    b'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>PyPI recent updates</title>'
    b"<item><title>177.0.0</title><link>https://pypi.org/project/openfisca-france/177.0.0/</link>"
    b"<pubDate>Mon, 19 Oct 2026 09:00:00 GMT</pubDate></item>"
    b"<item><title>176.1.2</title><link>https://pypi.org/project/openfisca-france/176.1.2/</link>"
    b"<pubDate>Fri, 02 Oct 2026 09:00:00 GMT</pubDate></item></channel></rss>"
)


class FakeFetcher:
    def __init__(self) -> None:
        self.responses: dict[str, netfetch.FetchResult | Exception] = {}
        self.calls: list[tuple[str, str | None, str | None]] = []

    def serve(self, url: str, body: bytes, status: int = 200, etag: str | None = None) -> None:
        self.responses[url] = netfetch.FetchResult(status, body, url, "application/rss+xml", etag, None)

    def __call__(self, url: str, etag: str | None, last_modified: str | None) -> netfetch.FetchResult:
        self.calls.append((url, etag, last_modified))
        result = self.responses.get(url, netfetch.FetchError("La source répond 404"))
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def fetcher(app, world: TestClient) -> FakeFetcher:  # type: ignore[no-untyped-def]
    fake = FakeFetcher()
    fake.serve(ECO, rss(LIVRET, FRENCH_TECH), etag='"v1"')
    fake.serve(PYPI, ENGINE)
    fake.serve(SERVICE_PUBLIC, rss())  # flux vide ici ; son vrai contenu a son propre test
    app.state.watch_fetcher = fake
    app.state.watch_resolver = lambda host: ["93.184.216.34"]
    return fake


def _refresh(world: TestClient, **body: Any) -> dict[str, Any]:
    r = world.post("/api/watch/refresh", json={"force": True, **body})
    assert r.status_code == 200, r.text
    return dict(r.json())


# ============================================================================ lecture des flux
def test_rss_from_the_ministry_is_read_and_dated() -> None:
    entries = dw.parse_feed(rss(LIVRET, FRENCH_TECH))
    assert [e.title for e in entries] == ["Livret A et LEP : les nouveaux taux au 1er février", FRENCH_TECH[0]]
    first = entries[0]
    assert first.summary == "Le taux du livret A évolue."  # balises retirées, texte brut
    assert first.published == datetime(2026, 10, 21, 13, 12, 28, tzinfo=UTC)  # heure de Paris (UTC+2 en octobre)
    assert first.guid == "000111" and first.link == LIVRET[1]


def test_atom_and_rss1_are_read_too() -> None:
    atom = (
        b'<feed xmlns="http://www.w3.org/2005/Atom"><title>t</title>'
        b'<entry><id>urn:1</id><title type="html">Pr&#233;l&#232;vement &#224; la source</title>'
        b'<link rel="related" href="https://example.org/autre"/><link href="https://example.org/a"/>'
        b"<published>2026-09-01T08:00:00Z</published><summary>Imp&#244;t</summary></entry></feed>"
    )
    (entry,) = dw.parse_feed(atom)
    assert entry.title == "Prélèvement à la source" and entry.link == "https://example.org/a"
    assert entry.published == datetime(2026, 9, 1, 8, tzinfo=UTC) and entry.guid == "urn:1"
    rdf = (
        b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/" '
        b'xmlns:dc="http://purl.org/dc/elements/1.1/"><item><title>Ch\xc3\xa8que \xc3\xa9nergie</title>'
        b"<link>https://example.org/c</link><dc:date>2026-04-02</dc:date></item></rdf:RDF>"
    )
    (item,) = dw.parse_feed(rdf)
    assert item.title == "Chèque énergie" and item.published == datetime(2026, 4, 1, 22, tzinfo=UTC)


def test_hostile_feeds_are_refused_or_neutralised() -> None:
    laughs = (
        b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;">]>'
        b"<rss><channel><item><title>&lol2;</title></item></channel></rss>"
    )
    xxe = (
        b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        b"<rss><channel><item><title>&x;</title></item></channel></rss>"
    )
    for body in (laughs, xxe, b"<html><body>pas un flux</body></html>", b"\x00\x01 pas du xml"):
        with pytest.raises(dw.FeedError):
            dw.parse_feed(body)
    tricky = (
        b"<rss><channel><item><title>Offre <![CDATA[<script>alert(1)</script>]]></title>"
        b"<link>javascript:alert(1)</link><guid>a</guid></item>"
        b"<item><title>doublon</title><guid>a</guid></item></channel></rss>"
    )
    (entry,) = dw.parse_feed(tricky)  # le doublon de guid est ignoré
    assert "<script>" not in entry.title and entry.title.startswith("Offre") and entry.link is None
    many = "".join(f"<item><title>n{i}</title><guid>{i}</guid></item>" for i in range(150))
    assert len(dw.parse_feed(f"<rss><channel>{many}</channel></rss>".encode())) == dw.MAX_ENTRIES


@given(st.text(max_size=3_000))
def test_clean_text_is_bounded_plain_text(raw: str) -> None:
    out = dw.clean_text(raw, 200)
    assert len(out) <= 200 and "\x00" not in out and "\x1b" not in out


@given(st.text(max_size=300))
def test_only_web_links_survive(raw: str) -> None:
    link = dw.safe_link(raw)
    assert link is None or link.lower().startswith(("http://", "https://"))


def test_topics_versions_and_dates() -> None:
    assert dw.topics_of("Livret A : le taux passe à 1,5 %") == ["epargne"]
    assert set(dw.topics_of("Prime d'activité et chèque énergie : ce qui change")) == {"aides", "energie"}
    assert dw.topics_of("La French tech") == []
    assert dw.relevant(["epargne"], {"epargne", "impots"}) and not dw.relevant([], {"epargne"})
    assert dw.latest_version(["176.1.2", "177.0.0", "177.1.0rc1", "pas une version", "99.9"]) == "177.0.0"
    assert dw.version_key("176.1.2") < dw.version_key("176.10.0")
    assert dw.parse_date("Mon, 19 Oct 2026 09:00:00 GMT") == datetime(2026, 10, 19, 9, tzinfo=UTC)
    assert dw.parse_date("n'importe quoi") is None and dw.parse_date("1789-07-14") is None


def test_reference_data_is_flagged_when_it_may_have_changed() -> None:
    rates = {"as_of": "2026-08-01", "source": "info.gouv.fr", "income_tax": {"year": 2026}}
    sources = [("prime_activite", "Fiche Caf", "2026-10-21")]
    now = {r.key: r for r in dw.reference_checks(rates, sources, date(2026, 10, 21))}
    assert now["rates"].status == "ok" and now["rates"].next_check == date(2027, 2, 1)
    assert now["income_tax"].status == "ok" and now["source:prime_activite"].next_check == date(2027, 4, 19)
    later = {r.key: r for r in dw.reference_checks(rates, sources, date(2027, 2, 1))}
    assert later["rates"].status == "stale" and "révisés" in later["rates"].detail
    assert later["income_tax"].status == "stale" and later["source:prime_activite"].status == "ok"
    assert dw.next_semester(date(2026, 1, 31)) == date(2026, 2, 1) and dw.next_semester(date(2026, 8, 1)) == date(
        2027, 2, 1
    )


# ============================================================================ réseau : jamais vers le LAN
@pytest.mark.parametrize(
    "url",
    [
        "http://www.economie.gouv.fr/rss",
        "https://user:pass@example.org/feed",
        "https://example.org:8443/feed",
        "https://localhost/feed",
        "https://nas.local/feed",
        "https://intranet/feed",
        "https://10.0.0.8/feed",
        "https://127.0.0.1/feed",
        "https://[::1]/feed",
        "https://169.254.169.254/latest/meta-data",
        "ftp://example.org/feed",
    ],
)
def test_check_url_refuses_anything_local_or_unusual(url: str) -> None:
    with pytest.raises(netfetch.FetchError):
        netfetch.check_url(url, lambda host: ["93.184.216.34"])


def test_dns_answers_are_checked_too() -> None:
    with pytest.raises(netfetch.FetchError):
        netfetch.check_url("https://piege.example.org/feed", lambda host: ["93.184.216.34", "192.168.1.10"])
    parts, ip = netfetch.check_url("https://example.org/feed", lambda host: ["93.184.216.34"])
    assert parts.hostname == "example.org" and ip == "93.184.216.34"


def _client(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


class Chunks(httpx.SyncByteStream):
    """Corps livré par morceaux, comme sur le réseau (une réponse en mémoire serait déjà lue par httpx)."""

    def __init__(self, data: bytes, size: int = 16_384) -> None:
        self.data, self.size = data, size

    def __iter__(self) -> Iterator[bytes]:
        for i in range(0, len(self.data), self.size):
            yield self.data[i : i + self.size]


def _resp(status: int, body: bytes = b"", headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, stream=Chunks(body), headers=headers or {})


def test_fetch_pins_the_checked_address_and_keeps_the_name_for_tls() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _resp(200, b"<rss/>", {"content-type": "application/rss+xml; charset=utf-8"})

    result = netfetch.fetch(
        "https://example.org/feed?x=1",
        http=_client(handler),
        resolver=lambda host: ["93.184.216.34"],
        etag='"v1"',
        pin=True,
    )
    assert result.status == 200 and result.body == b"<rss/>" and result.content_type == "application/rss+xml"
    request = seen[0]
    assert request.url.host == "93.184.216.34" and request.url.query == b"x=1"
    assert request.headers["host"] == "example.org" and request.extensions["sni_hostname"] == "example.org"
    assert request.headers["if-none-match"] == '"v1"'


def test_fetch_follows_only_safe_redirects() -> None:
    def to_lan(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://10.0.0.8/admin"})

    with pytest.raises(netfetch.FetchError, match="locale"):
        netfetch.fetch("https://example.org/feed", http=_client(to_lan), resolver=lambda h: ["93.184.216.34"])

    hops: list[str] = []

    def loop(request: httpx.Request) -> httpx.Response:
        hops.append(str(request.url))
        return httpx.Response(301, headers={"location": f"/next{len(hops)}"})

    with pytest.raises(netfetch.FetchError, match="redirections"):
        netfetch.fetch("https://example.org/feed", http=_client(loop), resolver=lambda h: ["93.184.216.34"])
    assert len(hops) == 4  # l'adresse d'origine et trois redirections, pas une de plus

    def moved(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "https://example.org/new"})
        return httpx.Response(304)

    result = netfetch.fetch(
        "https://example.org/old", http=_client(moved), resolver=lambda h: ["93.184.216.34"], pin=False
    )
    assert result.status == 304 and result.url == "https://example.org/new"


def test_fetch_bounds_size_time_and_compression() -> None:
    def big(request: httpx.Request) -> httpx.Response:
        return _resp(200, b"x" * 2_000)

    with pytest.raises(netfetch.FetchError, match="volumineuse"):
        netfetch.fetch(
            "https://example.org/f", http=_client(big), resolver=lambda h: ["93.184.216.34"], max_bytes=1_000
        )
    bomb = gzip.compress(b"\x00" * 5_000_000)  # 5 Mo une fois décompressés, quelques Ko sur le réseau

    def compressed(request: httpx.Request) -> httpx.Response:
        return _resp(200, bomb, {"content-encoding": "gzip"})

    with pytest.raises(netfetch.FetchError, match="volumineuse"):
        netfetch.fetch(
            "https://example.org/f", http=_client(compressed), resolver=lambda h: ["93.184.216.34"], max_bytes=100_000
        )
    small = gzip.compress(b"<rss/>")

    def ok(request: httpx.Request) -> httpx.Response:
        return _resp(200, small, {"content-encoding": "gzip"})

    assert (
        netfetch.fetch("https://example.org/f", http=_client(ok), resolver=lambda h: ["93.184.216.34"]).body
        == b"<rss/>"
    )

    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with pytest.raises(netfetch.FetchError, match="500"):
        netfetch.fetch("https://example.org/f", http=_client(broken), resolver=lambda h: ["93.184.216.34"])

    def odd(request: httpx.Request) -> httpx.Response:
        return _resp(200, b"abc", {"content-encoding": "br"})

    with pytest.raises(netfetch.FetchError, match="Encodage"):
        netfetch.fetch("https://example.org/f", http=_client(odd), resolver=lambda h: ["93.184.216.34"])

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(netfetch.FetchError, match="injoignable"):
        netfetch.fetch("https://example.org/f", http=_client(down), resolver=lambda h: ["93.184.216.34"])


# ============================================================================ scénario A : de la source à toi
def test_official_news_is_picked_up_dated_and_matched_to_you(app, world: TestClient, fetcher: FakeFetcher) -> None:  # type: ignore[no-untyped-def]
    data = _refresh(world)
    assert data["report"]["checked"] == 3 and data["report"]["new"] == 4 and data["report"]["errors"] == 0
    assert all(
        url in (ECO, PYPI, SERVICE_PUBLIC) for url, _, _ in fetcher.calls
    )  # seules des adresses publiques connues
    items = data["items"]
    livret = items[0]
    assert livret["title"].startswith("Livret A et LEP")  # pertinent pour toi : en premier
    assert livret["relevant"] is True and livret["topic_labels"] == ["Épargne"]
    assert livret["source"] == {"name": "Ministère de l'Économie — toutes les actualités", "trust": "officiel"}
    assert livret["published_at"].startswith("2026-10-21T13:12:28") and livret["version"] == 1
    tech = next(i for i in items if i["title"].startswith("La French tech"))
    assert tech["relevant"] is False
    assert data["counts"]["new_relevant"] == 1
    assert not any(i["title"] in ("177.0.0", "176.1.2") for i in items)  # les versions vont dans « moteur »
    assert "epargne" in data["interests"] and "aides" in data["interests"] and "emploi" in data["interests"]
    statuses = {r["key"]: r["status"] for r in data["references"]}
    assert statuses["rates"] == "ok" and any(k.startswith("source:") for k in statuses)
    assert len(statuses) == len(data["references"])  # une clé par donnée (l'écran s'en sert comme identifiant)
    eco = next(s for s in data["sources"] if s["url"] == ECO)
    assert eco["builtin"] and eco["last_ok_at"] and eco["last_error"] is None


def test_service_public_news_are_read_and_filtered_like_the_others(world: TestClient, fetcher: FakeFetcher) -> None:
    """Le vrai flux d'actualités de Service-Public (relevé le 24 octobre 2026, quatre articles) : lu, daté, classé par
    thème ; ce qui ne te concerne pas reste hors de « pertinent » (#32)."""
    fetcher.serve(SERVICE_PUBLIC, SP_FEED)
    data = _refresh(world)
    sp = [i for i in data["items"] if i["source"]["name"].startswith("Service-Public")]
    assert len(sp) == 4 and data["report"]["errors"] == 0
    prime = next(i for i in sp if i["title"].startswith("Prime de transport"))
    assert prime["url"].startswith("https://www.service-public.gouv.fr/particuliers/actualites/A18926")
    assert prime["published_at"].startswith("2026-10-23T22:00") and set(prime["topics"]) == {"energie", "emploi"}
    assert prime["relevant"] is True and prime["source"]["trust"] == "officiel"
    reversion = next(i for i in sp if i["title"].startswith("Retraite de réversion"))
    assert reversion["topics"] == ["retraite"] and reversion["relevant"] is False, "tu n'as pas l'âge ni de pension"
    netent = next(i for i in sp if "Net-entreprises" in i["title"])
    assert netent["topics"] == [] and netent["relevant"] is False


def test_changes_are_detected_and_versioned(app, world: TestClient, fetcher: FakeFetcher) -> None:  # type: ignore[no-untyped-def]
    _refresh(world)
    livret = next(i for i in world.get("/api/watch").json()["items"] if i["title"].startswith("Livret A"))
    assert world.patch(f"/api/watch/items/{livret['id']}", json={"status": "seen"}).status_code == 204
    updated = (LIVRET[0].replace("1er février", "1er février 2027"), *LIVRET[1:])
    fetcher.serve(ECO, rss(updated, FRENCH_TECH), etag='"v2"')
    data = _refresh(world)
    assert data["report"]["changed"] == 1 and data["report"]["new"] == 0
    again = next(i for i in data["items"] if i["id"] == livret["id"])
    assert again["version"] == 2 and again["changed_at"] and again["status"] == "new"  # revu → nouveau
    assert again["title"].endswith("1er février 2027")
    # Rien n'a changé (304) : la vérification est datée, sans rien retélécharger
    assert [etag for url, etag, _ in fetcher.calls if url == ECO][1] == '"v1"'  # la relève suivante envoie l'ETag
    fetcher.serve(ECO, b"", status=304)
    before = again["last_verified_at"]
    third = _refresh(world)
    after = next(i for i in third["items"] if i["id"] == livret["id"])
    assert third["report"]["changed"] == 0 and after["last_verified_at"] > before


def test_engine_updates_are_compared_with_the_version_you_use(app, world: TestClient, fetcher: FakeFetcher) -> None:  # type: ignore[no-untyped-def]
    with app.state.sessionmaker() as db:
        db.add(FiscalSimulation(period="2026-10", result=json.dumps({"engine_version": "176.1.2", "estimates": []})))
        db.commit()
    engine = _refresh(world)["engine"]
    assert engine["used"] == "176.1.2" and engine["latest"] == "177.0.0" and engine["outdated"] is True
    assert engine["latest_published_at"].startswith("2026-10-19") and "make fiscal-test" in engine["advice"]


def test_failures_stay_readable_and_isolated(app, world: TestClient, fetcher: FakeFetcher) -> None:  # type: ignore[no-untyped-def]
    fetcher.responses[ECO] = netfetch.FetchError("La source répond 503")
    data = _refresh(world)
    assert data["report"]["errors"] == 1 and data["report"]["new"] == 2  # le moteur a été relevé quand même
    eco = next(s for s in data["sources"] if s["url"] == ECO)
    assert eco["last_error"] == "La source répond 503" and eco["last_ok_at"] is None
    fetcher.serve(ECO, b"<html>maintenance</html>")
    eco = next(s for s in _refresh(world)["sources"] if s["url"] == ECO)
    assert eco["last_error"] == "Ce n'est pas un flux RSS ou Atom"


def test_watch_respects_consent_and_schedule(app, world: TestClient, fetcher: FakeFetcher) -> None:  # type: ignore[no-untyped-def]
    assert world.put("/api/consents/watch", json={"granted": False}).status_code == 200
    data = _refresh(world)
    assert data["report"]["skipped"] == 1 and fetcher.calls == [] and data["enabled"] is False
    world.put("/api/consents/watch", json={"granted": True})
    with app.state.sessionmaker() as db:
        now = utcnow()
        first = ws.housekeeping(db, now=now, fetcher=fetcher)
        assert first["watch_checked"] == 3
        assert ws.housekeeping(db, now=now + timedelta(hours=1), fetcher=fetcher)["watch_checked"] == 0
        assert ws.housekeeping(db, now=now + timedelta(hours=13), fetcher=fetcher)["watch_checked"] == 2  # 12 h
        old = db.scalars(select(WatchItem)).first()
        assert old is not None
        old.first_seen_at = now - timedelta(days=400)
        db.flush()
        assert ws.prune(db, now) == 1
        db.commit()


def test_user_sources_are_validated_and_manageable(app, world: TestClient, fetcher: FakeFetcher) -> None:  # type: ignore[no-untyped-def]
    assert world.post("/api/watch/sources", json={"url": "http://blog.example.org/feed"}).status_code == 422
    app.state.watch_resolver = lambda host: ["192.168.1.20"]
    assert world.post("/api/watch/sources", json={"url": "https://nas.example.org/feed"}).status_code == 422
    app.state.watch_resolver = lambda host: ["93.184.216.34"]
    r = world.post(
        "/api/watch/sources", json={"url": "https://blog.example.org/feed", "name": "Mon blog <b>budget</b>"}
    )
    assert r.status_code == 201 and r.json()["name"] == "Mon blog budget"
    assert world.post("/api/watch/sources", json={"url": "https://blog.example.org/feed"}).status_code == 409
    fetcher.serve(
        "https://blog.example.org/feed", rss(("Résilier sa box", "https://blog.example.org/p-000333", "", ""))
    )
    item = next(i for i in _refresh(world)["items"] if i["title"] == "Résilier sa box")
    assert item["source"]["trust"] == "perso" and item["topics"] == ["conso"]
    sources = world.get("/api/watch").json()["sources"]
    builtin = next(s for s in sources if s["builtin"])
    assert world.delete(f"/api/watch/sources/{builtin['id']}").status_code == 409
    assert world.patch(f"/api/watch/sources/{builtin['id']}", json={"enabled": False}).status_code == 204
    mine = next(s for s in sources if not s["builtin"])
    assert world.delete(f"/api/watch/sources/{mine['id']}").status_code == 204
    fetcher.calls.clear()
    _refresh(world)
    assert builtin["url"] not in [u for u, _, _ in fetcher.calls]  # désactivée : plus interrogée
    with app.state.sessionmaker() as db:
        assert db.scalar(select(WatchSource).where(WatchSource.url == "https://blog.example.org/feed")) is None


def test_items_can_be_dismissed_or_all_marked_seen(world: TestClient, fetcher: FakeFetcher) -> None:
    items = _refresh(world)["items"]
    assert world.patch(f"/api/watch/items/{items[0]['id']}", json={"status": "dismissed"}).status_code == 204
    assert items[0]["id"] not in [i["id"] for i in world.get("/api/watch").json()["items"]]
    assert world.patch("/api/watch/items/999999", json={"status": "seen"}).status_code == 404
    assert world.patch(f"/api/watch/items/{items[1]['id']}", json={"status": "lu"}).status_code == 422
    assert world.post("/api/watch/items/seen").json()["seen"] >= 1
    assert world.get("/api/watch").json()["counts"]["new"] == 0


def test_manual_refresh_is_rate_limited(world: TestClient, fetcher: FakeFetcher) -> None:
    codes = [world.post("/api/watch/refresh", json={}).status_code for _ in range(7)]
    assert codes[:6] == [200] * 6 and codes[6] == 429


def test_news_about_aids_after_your_simulation_asks_to_run_it_again(
    app, world: TestClient, fetcher: FakeFetcher
) -> None:  # type: ignore[no-untyped-def]
    """Scénario A jusqu'au bout : la source officielle publie sur la prime d'activité après ta simulation →
    Bank le relève, le rapproche de ta situation et te propose de relancer la simulation, source à l'appui."""
    with app.state.sessionmaker() as db:
        db.add(FiscalSimulation(period="2026-10", result=json.dumps({"engine_version": "176.1.2", "estimates": []})))
        db.commit()
    ppa = (
        "Prime d'activité : ce qui change au 1er janvier",
        "https://www.economie.gouv.fr/particuliers/ppa-000444",
        "Revalorisation.",
        "2026-10-21 09:00:00",
    )
    fetcher.serve(ECO, rss(LIVRET, ppa))
    item = next(i for i in _refresh(world)["items"] if i["title"].startswith("Prime d'activité"))
    assert item["relevant"] is True and "Aides et prestations" in item["topic_labels"]
    opps = {o["key"]: o for o in world.get("/api/opportunities").json()["opportunities"]}
    key = next(k for k in opps if k.startswith("aid:resimulate:"))
    lead = opps[key]
    assert lead["action"] == {"link": "#/aides", "label": "Relancer la simulation"}
    assert lead["sources"][0]["url"] == ppa[1] and "Prime d'activité" in lead["summary"]
    assert world.get("/api/copilot/summary").json()["watch"]["new_relevant"] >= 1
    # Masquer la nouveauté retire la piste ; une nouvelle simulation aussi
    world.patch(f"/api/watch/items/{item['id']}", json={"status": "dismissed"})
    assert not any(
        k.startswith("aid:resimulate:")
        for k in [o["key"] for o in world.get("/api/opportunities").json()["opportunities"]]
    )
