"""Live-Orderweg ohne Datenbank: Kraken-Signatur, Nonce, Fehlerabbildung, Client gegen aufgezeichnete Antworten,
harte Sperren einzeln, Strukturtests T3 und Geheimnis-Suchtest T24 (Teil Client/Logs)."""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

import httpx
import pytest

from tradingengine.live import locks
from tradingengine.live.exchange import (
    InvalidNonce,
    MarketRestricted,
    NotSent,
    OrderRequest,
    PermissionDenied,
    RateLimited,
    Rejected,
    Unavailable,
    Uncertain,
    asset_balance,
)
from tradingengine.live.kraken_private import KrakenPrivate, NonceSource, map_errors, sign
from tradingengine.live.locks import AccountLocks, Mandate, account_lock_reasons, strategy_lock_reasons
from tradingengine.live.manager import cl_ord_id
from tradingengine.live.session import open_live_exchange

SRC = Path(__file__).resolve().parents[1] / "src" / "tradingengine"
API = Path(__file__).resolve().parents[1] / "api"
FAKE_KEY = "FAKEKEY-T24-abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJ"
FAKE_SECRET = "kQH5HW/8p1uGOVjbgWA7FunAmGO8lsSUXNsu3eow76sz84Q18fWxnyRzBHCd3pd5nE9qa99HAZtuZuj6F1huXg=="
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


# ───────────── Signatur, Nonce, Fehler ─────────────


def test_signature_matches_kraken_documentation_example() -> None:
    # docs.kraken.com/api/docs/guides/spot-rest-auth (Beispiel «buy 1.25 XBTUSD at $37,500»)
    postdata = "nonce=1616492376594&ordertype=limit&pair=XBTUSD&price=37500&type=buy&volume=1.25"
    assert sign("/0/private/AddOrder", postdata, "1616492376594", FAKE_SECRET) == (
        "4/dpxb3iT4tp/ZCVEwSnEsLxx0bqyhLpdfOpc6fn7OR8+UClSV5n9E6aSS8MPtnRfp32bAb0nmbRn6H8ndwLUQ=="
    )


def test_nonce_strictly_increasing_even_if_clock_goes_back() -> None:
    times = iter([100.0, 100.0, 99.0, 101.0])
    src = NonceSource(clock=lambda: next(times))
    values = [src.next() for _ in range(4)]
    assert values == sorted(values) and len(set(values)) == 4


@pytest.mark.parametrize(
    ("code", "write", "cls"),
    [
        ("EAPI:Invalid nonce", True, InvalidNonce),
        ("EAPI:Rate limit exceeded", False, RateLimited),
        ("EOrder:Rate limit exceeded", True, RateLimited),
        ("EService: Throttled: 1700000000", True, RateLimited),
        ("EGeneral:Permission denied", True, PermissionDenied),
        ("EService:Market in cancel_only mode", True, MarketRestricted),
        ("EService:Unavailable", True, Uncertain),
        ("EService:Unavailable", False, Unavailable),
        ("EService:Deadline elapsed", True, Rejected),
        ("EOrder:Insufficient funds", True, Rejected),
        ("EGeneral:Invalid arguments:volume", True, Rejected),
        ("EWeird:Something", True, Uncertain),
    ],
)
def test_error_mapping(code: str, write: bool, cls: type) -> None:
    err = map_errors([code], write)
    assert type(err) is cls and err.code == code


def test_invalid_nonce_and_rate_limit_count_as_definite_rejection() -> None:
    # Für AddOrder heisst das: sicher nicht platziert (kein UNKNOWN, keine Doppelorder)
    assert isinstance(map_errors(["EAPI:Invalid nonce"], True), Rejected)
    assert isinstance(map_errors(["EAPI:Rate limit exceeded"], True), Rejected)


# ───────────── Client gegen aufgezeichnete Antworten ─────────────


class Recorder:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        name = request.url.path.rsplit("/", 1)[-1]
        r = self.responses[name]
        if isinstance(r, Exception):
            raise r
        return httpx.Response(200, json=r)


def client(rec: Recorder) -> KrakenPrivate:
    http = httpx.Client(base_url="https://api.kraken.com", transport=httpx.MockTransport(rec))
    return KrakenPrivate(FAKE_KEY, FAKE_SECRET, client=http)


def test_add_order_sends_cl_ord_id_deadline_and_signs() -> None:
    rec = Recorder({"AddOrder": {"error": [], "result": {"descr": {"order": "buy"}, "txid": ["OABC-123"]}}})
    c = client(rec)
    deadline = NOW + timedelta(seconds=15)
    txid = c.add_order(OrderRequest("XBTUSD", "buy", "limit", D("0.0100"), "te0123456789abcdef", price=D("60000.0"), deadline=deadline,
                                    timeinforce="GTD", expiretm=NOW + timedelta(hours=1)))
    assert txid == "OABC-123"
    req = rec.requests[0]
    body = urllib.parse.parse_qs(req.content.decode())
    assert body["cl_ord_id"] == ["te0123456789abcdef"] and body["volume"] == ["0.01"] and body["price"] == ["60000"]
    assert body["deadline"] == ["2026-10-04T12:00:15.000000Z"] and body["timeinforce"] == ["GTD"] and "validate" not in body
    assert req.headers["API-Key"] == FAKE_KEY
    assert req.headers["API-Sign"] == sign("/0/private/AddOrder", req.content.decode(), body["nonce"][0], FAKE_SECRET)


def test_validate_flag_is_sent() -> None:
    rec = Recorder({"AddOrder": {"error": [], "result": {"descr": {"order": "buy"}}}})
    assert client(rec).add_order(OrderRequest("XBTUSD", "buy", "limit", D("0.0001"), "permcheck", price=D(1), validate=True)) == "VALIDATED"
    assert urllib.parse.parse_qs(rec.requests[0].content.decode())["validate"] == ["true"]


def test_transport_failures_map_to_not_sent_or_uncertain() -> None:
    req = OrderRequest("XBTUSD", "buy", "limit", D(1), "te0000000000000001", price=D(1))
    with pytest.raises(NotSent):
        client(Recorder({"AddOrder": httpx.ConnectError("refused")})).add_order(req)
    with pytest.raises(Uncertain):
        client(Recorder({"AddOrder": httpx.ReadTimeout("timeout")})).add_order(req)
    with pytest.raises(Uncertain):
        client(Recorder({"AddOrder": {"error": ["EService:Unavailable"]}})).add_order(req)
    with pytest.raises(Unavailable):
        client(Recorder({"Balance": httpx.ReadTimeout("timeout")})).balances()


def test_parses_orders_trades_and_balances() -> None:
    order = {"status": "open", "cl_ord_id": "te0000000000000001", "descr": {"pair": "XBTUSD", "type": "sell", "ordertype": "stop-loss"},
             "vol": "0.5", "vol_exec": "0.1", "price": "59000", "stopprice": "58000", "opentm": 1759579200.1, "trades": ["T1"]}
    rec = Recorder({
        "OpenOrders": {"error": [], "result": {"open": {"OX-1": order}}},
        "TradesHistory": {"error": [], "result": {"trades": {"T1": {"ordertxid": "OX-1", "pair": "XXBTZUSD", "time": 1759579201.5, "type": "sell",
                                                                     "price": "59000", "vol": "0.1", "fee": "23.6"}}, "count": 1}},
        "Balance": {"error": [], "result": {"XXBT": "0.5", "ZUSD": "100.25", "XBT.F": "1.0"}},
        "WithdrawMethods": {"error": ["EGeneral:Permission denied"]},
    })
    c = client(rec)
    [o] = c.open_orders()
    assert (o.txid, o.cl_ord_id, o.side, o.ordertype, o.vol, o.vol_exec, o.stopprice, o.trade_ids) == (
        "OX-1", "te0000000000000001", "sell", "stop-loss", D("0.5"), D("0.1"), D(58000), ("T1",))
    [t] = c.trades()
    assert (t.trade_id, t.ordertxid, t.vol, t.fee) == ("T1", "OX-1", D("0.1"), D("23.6"))
    bal = c.balances()
    assert asset_balance(bal, "BTC") == D("0.5") and asset_balance(bal, "USD") == D("100.25")  # Earn-Guthaben (.F) zählt nicht
    assert c.probe_withdraw_permission() == "DENIED"


def test_t24_client_never_exposes_key_or_secret(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    rec = Recorder({"Balance": {"error": ["EAPI:Invalid key"]}, "AddOrder": httpx.ReadTimeout("t"), "OpenOrders": httpx.ConnectError("x")})
    c = client(rec)
    texts = [repr(c), str(c)]
    for call in (c.balances, c.open_orders, lambda: c.add_order(OrderRequest("XBTUSD", "buy", "limit", D(1), "te0000000000000002", price=D(1)))):
        try:
            call()
        except Exception as exc:  # noqa: BLE001 – wir sammeln die Texte aller Fehler
            texts.append(str(exc))
            texts.append(repr(exc))
    texts.append(caplog.text)
    blob = "\n".join(texts)
    assert FAKE_KEY not in blob and FAKE_SECRET not in blob and FAKE_SECRET[:20] not in blob
    for r in rec.requests:  # die Signatur steht nur im Header, nie in Logs
        assert r.headers["API-Sign"] not in blob


def test_cl_ord_id_is_deterministic_ascii_and_at_most_18_chars() -> None:
    a = cl_ord_id("L:1:abc:ENTRY")
    assert a == cl_ord_id("L:1:abc:ENTRY") and a != cl_ord_id("L:1:abd:ENTRY")
    assert len(a) == 18 and a.isascii() and re.fullmatch(r"te[0-9a-f]{16}", a)


# ───────────── harte Sperren einzeln ─────────────


def mandate(**kw: object) -> Mandate:
    base: dict[str, object] = dict(id=1, account_id="live-kraken", autonomy_level=3, strategy_version_ids=("s2-volume-breakout@1",),
                                   instrument_ids=("TEST:AAA/USD",), budget=D(1000), policy={}, status="ACTIVE", activated_at=NOW,
                                   activated_by="user:test", step_up_at=NOW - timedelta(minutes=1), valid_until=None)
    base.update(kw)
    return Mandate(**base)  # type: ignore[arg-type]


def all_green(**kw: object) -> AccountLocks:
    base: dict[str, object] = dict(
        enabled=True, keys=True, permissions={"checked_at": NOW.isoformat(), "withdraw": False, "trade": True, "key_fingerprint": "fp"},
        fingerprint="fp", mandate=mandate(), autopilot_state="ACTIVE", recovery_tick=False, last_recon_status="OK", last_recon_at=NOW,
        unknown_orders=0, test_ack_at=NOW - timedelta(days=1), fx_ok=True, supported_protection=frozenset({"STOP_AT_EXCHANGE"}),
    )
    base.update(kw)
    return AccountLocks(**base)  # type: ignore[arg-type]


def test_all_locks_green_means_no_reason() -> None:
    assert account_lock_reasons(all_green(), NOW) == []


@pytest.mark.parametrize(
    ("override", "fragment"),
    [
        ({"enabled": False}, "Sperre 1"),
        ({"keys": False}, "Sperre 2"),
        ({"permissions": None}, "Sperre 3"),
        ({"permissions": {"checked_at": "x", "withdraw": True, "trade": True, "key_fingerprint": "fp"}}, "Auszahlungsrecht"),
        ({"permissions": {"checked_at": "x", "withdraw": False, "trade": True, "key_fingerprint": "other"}}, "gewechselt"),
        ({"mandate": None}, "Sperre 4"),
        ({"mandate": mandate(status="SUSPENDED")}, "Sperre 4"),
        ({"mandate": mandate(activated_by=None)}, "Step-up"),
        ({"mandate": mandate(step_up_at=None)}, "Step-up"),
        ({"mandate": mandate(budget=D(0))}, "Budget"),
        ({"mandate": mandate(instrument_ids=())}, "Instrumente"),
        ({"mandate": mandate(autonomy_level=1)}, "Autonomiestufe 1"),
        ({"mandate": mandate(valid_until=NOW - timedelta(seconds=1))}, "abgelaufen"),
        ({"mandate": mandate(policy={"protection": "OCO_AT_EXCHANGE"})}, "nicht unterstützt"),
        ({"autopilot_state": "ENTRIES_PAUSED"}, "ENTRIES_PAUSED"),
        ({"recovery_tick": True}, "Wiederherstellung"),
        ({"last_recon_status": "DIFF"}, "Abgleich nicht OK"),
        ({"last_recon_at": NOW - timedelta(minutes=6)}, "älter als 5 min"),
        ({"unknown_orders": 1}, "unbekanntem Status"),
        ({"test_ack_at": None}, "Testalarm"),
        ({"test_ack_at": NOW - timedelta(days=8)}, "Testalarm"),
        ({"fx_ok": False}, "FX"),
    ],
)
def test_each_lock_blocks_entries_on_its_own(override: dict[str, object], fragment: str) -> None:
    reasons = account_lock_reasons(all_green(**override), NOW)
    assert reasons and any(fragment in r for r in reasons), reasons


def test_strategy_locks_need_approved_live_and_g1_to_g3() -> None:
    m = mandate()
    ok = {"G1": "PASSED", "G2": "PASSED", "G3": "PASSED"}
    assert strategy_lock_reasons("s2-volume-breakout@1", "TEST:AAA/USD", m, True, ok) == []
    assert any("APPROVED_LIVE" in r for r in strategy_lock_reasons("s2-volume-breakout@1", "TEST:AAA/USD", m, False, ok))
    assert any("G3" in r for r in strategy_lock_reasons("s2-volume-breakout@1", "TEST:AAA/USD", m, True, {**ok, "G3": "INSUFFICIENT"}))
    assert any("nicht im Mandat" in r for r in strategy_lock_reasons("s1-trend-pullback@1", "TEST:AAA/USD", m, True, ok))
    assert any("nicht im Mandat" in r for r in strategy_lock_reasons("s2-volume-breakout@1", "TEST:BBB/USD", m, True, ok))


def test_lock1_and_lock2_gate_client_construction() -> None:
    assert open_live_exchange({}) is None
    assert open_live_exchange({"LIVE_TRADING_ENABLED": "1", "KRAKEN_API_KEY": "k", "KRAKEN_API_SECRET": FAKE_SECRET}) is None
    assert open_live_exchange({"LIVE_TRADING_ENABLED": "true", "KRAKEN_API_KEY": "k"}) is None
    # im Worker-Projekt (ENGINE_ROLE fehlt oder «worker») entsteht nie ein Client, auch mit Schlüsseln und Flag
    assert open_live_exchange({"LIVE_TRADING_ENABLED": "true", "KRAKEN_API_KEY": "k", "KRAKEN_API_SECRET": FAKE_SECRET}) is None
    assert open_live_exchange({"LIVE_TRADING_ENABLED": "true", "KRAKEN_API_KEY": "k", "KRAKEN_API_SECRET": FAKE_SECRET, "ENGINE_ROLE": "live"}) is not None
    assert not locks.live_enabled({}) and locks.live_enabled({"LIVE_TRADING_ENABLED": "true"})


# ───────────── Strukturtests T3 ─────────────


def _python_files() -> list[Path]:
    return [p for p in SRC.rglob("*.py")] + [p for p in API.glob("*.py")]


def test_t3_real_client_is_constructed_only_in_session() -> None:
    hits = [p for p in _python_files() if re.search(r"KrakenPrivate\(", p.read_text(encoding="utf-8")) and p.name != "kraken_private.py"]
    assert [p.name for p in hits] == ["session.py"]


def test_t3_paper_signal_and_tick_paths_do_not_import_live_or_read_keys() -> None:
    for p in _python_files():
        rel = p.relative_to(p.parents[2] if p.parent.name != "api" else p.parents[1]).as_posix()
        text = p.read_text(encoding="utf-8")
        if "/live/" in f"/{rel}" or p.name == "live.py":
            continue
        imports = re.findall(r"^\s*(?:from|import)\s+(\S+)", text, flags=re.MULTILINE)
        assert not [i for i in imports if i.startswith(("tradingengine.live", ".live", "..live"))], rel
        assert "KRAKEN_API_KEY" not in text and "KRAKEN_API_SECRET" not in text, rel


def test_paper_process_leaves_live_commands_for_the_live_process() -> None:
    from tradingengine.live.commands import LIVE_TYPES
    from tradingengine.services.commands import LIVE_COMMAND_TYPES

    assert set(LIVE_TYPES) == set(LIVE_COMMAND_TYPES)


def test_t3_live_api_returns_disabled_without_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.util
    import io

    monkeypatch.delenv("LIVE_TRADING_ENABLED", raising=False)
    monkeypatch.setenv("CRON_SECRET", "s3cr3t")
    monkeypatch.setenv("ENGINE_ROLE", "live")
    spec = importlib.util.spec_from_file_location("live_api", API / "live.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    class H(mod.handler):  # type: ignore[name-defined,misc]
        def __init__(self, auth: str) -> None:
            self.headers = {"authorization": auth}  # type: ignore[assignment]
            self.wfile = io.BytesIO()
            self.status = 0

        def send_response(self, code: int, message: str | None = None) -> None:
            self.status = code

        def send_header(self, k: str, v: str) -> None:
            pass

        def end_headers(self) -> None:
            pass

    h = H("Bearer wrong")
    h.do_GET()
    assert h.status == 401
    h = H("Bearer s3cr3t")
    h.do_GET()
    assert h.status == 200 and json.loads(h.wfile.getvalue()) == {"live": "disabled"}


def test_vercel_config_has_separate_live_function() -> None:
    cfg = json.loads((API.parent / "vercel.json").read_text(encoding="utf-8"))
    assert cfg["functions"]["api/live.py"]["maxDuration"] == 60
    assert {"path": "/api/live", "schedule": "* * * * *"} in cfg["crons"]
