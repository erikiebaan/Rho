from datetime import date, datetime
import math
import threading
import time

import pandas as pd
import streamlit as st
from scipy.stats import norm

try:
    from ibapi.client import EClient
    from ibapi.contract import Contract
    from ibapi.wrapper import EWrapper
    IBAPI_AVAILABLE = True
except Exception:
    IBAPI_AVAILABLE = False

st.set_page_config(page_title="ATLAS RHO KR01 LAB", page_icon="◼", layout="centered", initial_sidebar_state="collapsed")

ACCENT = "#d8ff32"
st.markdown(f"""
<style>
:root{{color-scheme:dark}} .stApp{{background:#080a0d;color:#f7f8fa}}
.block-container{{max-width:760px;padding:1rem 1rem 5rem}}
header[data-testid="stHeader"]{{background:transparent}}
#MainMenu,footer,[data-testid='stToolbar'],[data-testid='stDecoration']{{display:none!important}}
.brand{{font-size:.72rem;letter-spacing:.24em;color:#8b929e;font-weight:700}}
.title{{font-size:2.0rem;font-weight:650;margin:.2rem 0 .2rem}}
.sub{{font-size:.82rem;color:#8b929e;margin-bottom:1.2rem}}
.section{{font-size:.72rem;letter-spacing:.13em;color:#7e8794;font-weight:700;margin:1.2rem 0 .55rem}}
.lock{{border:1px solid #2a3039;background:#11151b;border-radius:14px;padding:12px 14px;margin:.4rem 0 .8rem}}
.good{{color:{ACCENT};font-weight:800}}
.stButton>button{{border-radius:14px;min-height:48px;font-weight:750;border:0;background:{ACCENT};color:#090b0e}}
[data-testid="stDataFrame"]{{border-radius:14px;overflow:hidden}}
</style>
""", unsafe_allow_html=True)


def third_weekday(year, month, weekday):
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return date(year, month, 1 + offset + 14)


def third_wednesday(year, month):
    return third_weekday(year, month, 2)


def third_friday(year, month):
    return third_weekday(year, month, 4)


def quarter_months_between(start, expiry):
    out = []
    y, m = start.year, start.month
    while (y, m) <= (expiry.year, expiry.month):
        if m in (3, 6, 9, 12):
            d = third_wednesday(y, m)
            if start < d < expiry:
                out.append(d)
        m += 1
        if m == 13:
            m = 1
            y += 1
    return out


def default_curve_rows(valuation, expiry):
    ends = quarter_months_between(valuation, expiry) + [expiry]
    defaults = [2.0, 2.5, 3.5, 4.5, 3.5, 3.5, 3.5, 3.5]
    rows = []
    prev = valuation
    for i, end in enumerate(ends):
        rows.append({
            "Start": prev,
            "End": end,
            "Forward %": defaults[min(i, len(defaults)-1)],
        })
        prev = end
    return rows


def validate_curve(curve, valuation, expiry):
    x = curve.copy()
    x["Start"] = pd.to_datetime(x["Start"]).dt.date
    x["End"] = pd.to_datetime(x["End"]).dt.date
    x["Forward %"] = pd.to_numeric(x["Forward %"], errors="coerce")
    if x["Forward %"].isna().any():
        raise ValueError("Elke curve-bucket heeft een Forward % nodig.")
    x = x.sort_values("Start").reset_index(drop=True)
    if len(x) == 0:
        raise ValueError("Minimaal één curve-bucket nodig.")
    if x.iloc[0]["Start"] != valuation:
        raise ValueError("De eerste bucket moet op de valuation date starten.")
    for i in range(len(x)):
        if x.loc[i, "End"] <= x.loc[i, "Start"]:
            raise ValueError("Elke bucket moet na zijn startdatum eindigen.")
        if i and x.loc[i, "Start"] != x.loc[i-1, "End"]:
            raise ValueError("Curve-buckets moeten exact op elkaar aansluiten.")
    if x.iloc[-1]["End"] != expiry:
        raise ValueError("De laatste curve-bucket moet exact op option expiry eindigen.")
    return x


def integrated_rate(curve):
    total = 0.0
    for _, r in curve.iterrows():
        dt = (r["End"] - r["Start"]).days / 365.0
        total += float(r["Forward %"]) / 100.0 * dt
    return total


def option_price_points(spot, strike, vol, pv_div, curve, right):
    if spot <= 0 or strike <= 0 or vol <= 0:
        raise ValueError("Spot, strike en volatility moeten positief zijn.")
    expiry = curve.iloc[-1]["End"]
    valuation = curve.iloc[0]["Start"]
    T = max((expiry - valuation).days / 365.0, 1e-9)
    R = integrated_rate(curve)
    df = math.exp(-R)
    prepaid = spot - pv_div
    if prepaid <= 0:
        raise ValueError("Spot - PV dividends moet positief zijn.")
    forward = prepaid / df
    sigt = vol * math.sqrt(T)
    d1 = (math.log(forward / strike) + 0.5 * vol * vol * T) / sigt
    d2 = d1 - sigt
    if right.upper() == "C":
        return df * (forward * norm.cdf(d1) - strike * norm.cdf(d2))
    return df * (strike * norm.cdf(-d2) - forward * norm.cdf(-d1))


def bucketed_kr01(spot, strike, vol, pv_div, curve, right, multiplier, position, bump_bp=1.0):
    base = option_price_points(spot, strike, vol, pv_div, curve, right)
    rows = []
    for i, r in curve.iterrows():
        bumped = curve.copy()
        bumped.loc[i, "Forward %"] = float(bumped.loc[i, "Forward %"]) + bump_bp / 100.0
        px = option_price_points(spot, strike, vol, pv_div, bumped, right)
        pnl = (px - base) * multiplier * position
        rows.append({
            "Bucket": f"{r['Start'].strftime('%d-%m-%y')} → {r['End'].strftime('%d-%m-%y')}",
            "KR01 €": pnl / bump_bp,
        })
    return base, pd.DataFrame(rows)


class IBOptionSnapshot(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.ready = threading.Event()
        self.details_done = threading.Event()
        self.market_done = threading.Event()
        self.underlying_done = threading.Event()
        self.details = []
        self.model = {}
        self.prices = {}
        self.errors = []
        self.tick_log = []

    def nextValidId(self, orderId):
        self.ready.set()

    def error(self, reqId, *args):
        # Compatible with pre/post TWS API 10.33 error signatures.
        if len(args) >= 4 and isinstance(args[1], int):
            code, msg = args[1], str(args[2])
        elif len(args) >= 2 and isinstance(args[0], int):
            code, msg = args[0], str(args[1])
        else:
            return
        if code not in (2104, 2106, 2107, 2108, 2158):
            self.errors.append(f"{code}: {msg}")

    def contractDetails(self, reqId, contractDetails):
        self.details.append(contractDetails)

    def contractDetailsEnd(self, reqId):
        self.details_done.set()

    def tickPrice(self, reqId, tickType, price, attrib):
        self.tick_log.append({"reqId": reqId, "kind": "price", "tickType": tickType, "value": price})
        if price is not None and price > 0:
            self.prices.setdefault(reqId, {})[tickType] = float(price)
            if reqId in (7003, 7004, 7005, 7006) and tickType in (4, 68, 1, 2, 66, 67, 9, 75):
                self.underlying_done.set()

    def tickString(self, reqId, tickType, value):
        self.tick_log.append({"reqId": reqId, "kind": "string", "tickType": tickType, "value": value})

    def tickOptionComputation(self, reqId, tickType, tickAttrib, impliedVol, delta,
                              optPrice, pvDividend, gamma, vega, theta, undPrice):
        if tickType == 13 or not self.model:
            vals = {
                "impliedVol": impliedVol,
                "delta": delta,
                "optPrice": optPrice,
                "pvDividend": pvDividend,
                "gamma": gamma,
                "vega": vega,
                "theta": theta,
                "undPrice": undPrice,
            }
            self.model = {k: float(v) for k, v in vals.items() if v is not None and abs(v) < 1e307}
            if all(k in self.model for k in ("impliedVol", "optPrice", "undPrice")):
                self.market_done.set()


def _best_price(ticks):
    """Prefer last, then close, then bid/ask midpoint for an index snapshot."""
    if not ticks:
        return None
    for tick in (4, 68):  # LAST / DELAYED_LAST
        if ticks.get(tick, 0) > 0:
            return ticks[tick]
    for tick in (9, 75):  # CLOSE / DELAYED_CLOSE
        if ticks.get(tick, 0) > 0:
            return ticks[tick]
    bid = ticks.get(1) or ticks.get(66)
    ask = ticks.get(2) or ticks.get(67)
    if bid and ask:
        return (bid + ask) / 2.0
    return bid or ask


def _fetch_underlying_index(app, market_type, timeout):
    """Resolve the exact ESTX50 index contract first, then request its market data."""
    probe = Contract()
    probe.symbol = "ESTX50"
    probe.secType = "IND"
    probe.exchange = "EUREX"
    probe.currency = "EUR"

    app.details = []
    app.details_done.clear()
    app.reqContractDetails(7004, probe)
    app.details_done.wait(timeout)
    index_details = list(app.details)
    if not index_details:
        return None, {"contract": None, "ticks": {}, "errors": list(app.errors)}

    idx = index_details[0].contract

    # Try the requested feed first. If LIVE is not subscribed, IB can still
    # provide delayed/frozen data when that feed is available.
    requested = int(market_type)
    attempts = [(requested, 7003, "REQUESTED")]
    if requested == 1:
        attempts += [
            (2, 7004, "FROZEN"),
            (4, 7005, "DELAYED_FROZEN"),
            (3, 7006, "DELAYED"),
        ]
    elif requested == 2:
        attempts += [(4, 7005, "DELAYED_FROZEN"), (3, 7006, "DELAYED")]

    used = None
    used_ticks = {}
    attempt_log = []
    underlying = None
    for md_type, req_id, label in attempts:
        app.underlying_done.clear()
        app.prices.pop(req_id, None)
        err_start = len(app.errors)
        app.reqMarketDataType(md_type)
        app.reqMktData(req_id, idx, "", False, False, [])
        app.underlying_done.wait(min(timeout, 4.0))
        time.sleep(0.5)
        app.cancelMktData(req_id)
        ticks = dict(app.prices.get(req_id, {}))
        price = _best_price(ticks)
        attempt_log.append({
            "label": label,
            "marketDataType": md_type,
            "reqId": req_id,
            "price": price,
            "ticks": ticks,
            "tick_log": [x for x in app.tick_log if x.get("reqId") == req_id],
            "new_errors": app.errors[err_start:],
        })
        if price is not None:
            underlying = price
            used = {"label": label, "marketDataType": md_type, "reqId": req_id}
            used_ticks = ticks
            break

    diag = {
        "contract": {
            "conId": idx.conId,
            "symbol": idx.symbol,
            "localSymbol": idx.localSymbol,
            "secType": idx.secType,
            "exchange": idx.exchange,
            "primaryExchange": idx.primaryExchange,
            "currency": idx.currency,
        },
        "used_feed": used,
        "ticks": used_ticks,
        "attempts": attempt_log,
        "errors": list(app.errors),
    }
    return underlying, diag


def find_ib_option(host, port, client_id, expiry, strike, right, market_type=1, timeout=8.0):
    """Resolve an OESX option from human-readable fields; no conId lookup by user."""
    if not IBAPI_AVAILABLE:
        raise RuntimeError("IB TWS Python API is niet geïnstalleerd in deze Python-omgeving.")
    app = IBOptionSnapshot()
    app.connect(host, int(port), int(client_id))
    thread = threading.Thread(target=app.run, daemon=True)
    thread.start()
    if not app.ready.wait(timeout):
        app.disconnect()
        raise RuntimeError("Geen TWS/IB Gateway verbinding. Controleer API settings, host en poort.")

    c = Contract()
    c.symbol = "ESTX50"
    c.secType = "OPT"
    c.exchange = "EUREX"
    c.currency = "EUR"
    c.lastTradeDateOrContractMonth = expiry.strftime("%Y%m%d")
    c.strike = float(strike)
    c.right = right.upper()
    c.multiplier = "10"
    c.tradingClass = "OESX"

    app.reqContractDetails(7001, c)
    if not app.details_done.wait(timeout) or not app.details:
        err = "; ".join(app.errors[-3:])
        app.disconnect()
        raise RuntimeError(
            f"Geen OESX {expiry.strftime('%d-%m-%Y')} {strike:g} {right.upper()} gevonden."
            + (f" IB: {err}" if err else "")
        )

    # Exact match first. IB can occasionally return more than one detail record.
    exact = []
    for d in app.details:
        k = d.contract
        raw = (k.lastTradeDateOrContractMonth or "")[:8]
        if (
            raw == expiry.strftime("%Y%m%d")
            and abs(float(k.strike) - float(strike)) < 1e-9
            and k.right.upper() == right.upper()
            and (not k.tradingClass or k.tradingClass == "OESX")
        ):
            exact.append(d)
    if len(exact) != 1:
        app.disconnect()
        if not exact:
            raise RuntimeError("IB gaf contractdetails terug, maar geen unieke exacte OESX-match.")
        raise RuntimeError(f"IB vond {len(exact)} exacte matches; contractselectie is niet uniek.")

    contract = exact[0].contract
    app.reqMarketDataType(int(market_type))
    app.reqMktData(7002, contract, "", False, False, [])
    app.market_done.wait(timeout)
    time.sleep(0.5)
    app.cancelMktData(7002)

    # Do not rely on the option-computation undPrice: request ESTX50 itself.
    underlying, underlying_diag = _fetch_underlying_index(app, market_type, timeout)
    if underlying is None:
        # Last-resort fallback to a valid undPrice from the option model tick.
        candidate = app.model.get("undPrice")
        if candidate is not None and candidate > 0:
            underlying = float(candidate)
    app.disconnect()

    expiry_raw = contract.lastTradeDateOrContractMonth[:8]
    resolved_expiry = datetime.strptime(expiry_raw, "%Y%m%d").date() if len(expiry_raw) == 8 else None
    return {
        "conId": contract.conId,
        "symbol": contract.symbol,
        "localSymbol": contract.localSymbol,
        "expiry": resolved_expiry,
        "strike": float(contract.strike),
        "right": contract.right,
        "multiplier": float(contract.multiplier or 10),
        "exchange": contract.exchange,
        "currency": contract.currency,
        "model": app.model,
        "underlying": underlying,
        "underlying_diag": underlying_diag,
        "errors": app.errors,
    }


EURIBOR_TEST_CONTRACTS = [
    {"month": "Oct-26", "expiry": "202610", "role": "FRONT PROXY"},
    {"month": "Dec-26", "expiry": "202612", "role": "DEC→MAR"},
    {"month": "Mar-27", "expiry": "202703", "role": "MAR→JUN"},
    {"month": "Jun-27", "expiry": "202706", "role": "JUN→SEP"},
    {"month": "Sep-27", "expiry": "202709", "role": "SEP→DEC"},
]


def _resolve_euribor_future(app, expiry, timeout):
    """Resolve FEU3 by IB's product symbol EU3 + contract month; never rely on stored conIds."""
    attempts = [
        ("EU3", "EUREX"),
        ("EU3", "EUREXEU"),
    ]
    diagnostics = []
    for symbol, exchange in attempts:
        app.details = []
        app.details_done.clear()
        q = Contract()
        q.symbol = symbol
        q.secType = "FUT"
        q.exchange = exchange
        q.currency = "EUR"
        q.lastTradeDateOrContractMonth = expiry
        req_id = 7200 + len(diagnostics)
        err_start = len(app.errors)
        app.reqContractDetails(req_id, q)
        app.details_done.wait(timeout)
        details = list(app.details)
        diagnostics.append({
            "symbol": symbol,
            "exchange": exchange,
            "expiry": expiry,
            "matches": len(details),
            "errors": app.errors[err_start:],
        })
        if details:
            # Prefer exact contractMonth, then local-symbol month encoded by IB.
            exact = [
                d for d in details
                if (getattr(d, "contractMonth", "") or "").startswith(expiry)
                or (d.contract.lastTradeDateOrContractMonth or "").startswith(expiry)
            ]
            d = exact[0] if exact else details[0]
            return d.contract, diagnostics
    return None, diagnostics


def fetch_euribor_strip(host, port, client_id, market_type=1, timeout=6.0):
    """Resolve current 3M Euribor futures dynamically in TWS, then fetch a usable quote."""
    if not IBAPI_AVAILABLE:
        raise RuntimeError("IB TWS Python API is niet geïnstalleerd in deze Python-omgeving.")
    app = IBOptionSnapshot()
    app.connect(host, int(port), int(client_id))
    thread = threading.Thread(target=app.run, daemon=True)
    thread.start()
    if not app.ready.wait(timeout):
        app.disconnect()
        raise RuntimeError("Geen TWS/IB Gateway verbinding voor Euribor strip.")

    rows = []
    requested = int(market_type)
    md_attempts = [requested]
    if requested == 1:
        md_attempts += [2, 4, 3]
    elif requested == 2:
        md_attempts += [4, 3]

    for j, spec in enumerate(EURIBOR_TEST_CONTRACTS):
        contract, resolve_diag = _resolve_euribor_future(app, spec["expiry"], timeout)
        if contract is None:
            rows.append({
                **spec, "conId": None, "resolved_local": None, "price": None,
                "rate": None, "feed": None, "status": "CONTRACT NOT FOUND",
                "resolve_diag": resolve_diag,
            })
            continue

        price = None
        feed_used = None
        quote_diag = []
        for k, md in enumerate(md_attempts):
            req_id = 7300 + j * 10 + k
            app.prices.pop(req_id, None)
            err_start = len(app.errors)
            app.reqMarketDataType(md)
            app.reqMktData(req_id, contract, "", False, False, [])
            time.sleep(1.2)
            app.cancelMktData(req_id)
            ticks = dict(app.prices.get(req_id, {}))
            px = _best_price(ticks)
            quote_diag.append({
                "marketDataType": md,
                "ticks": ticks,
                "errors": app.errors[err_start:],
            })
            if px is not None:
                price = float(px)
                feed_used = {1:"LIVE", 2:"FROZEN", 3:"DELAYED", 4:"DELAYED FROZEN"}.get(md, str(md))
                break

        rows.append({
            **spec,
            "conId": int(contract.conId),
            "resolved_local": contract.localSymbol,
            "price": price,
            "rate": (100.0 - price) if price is not None else None,
            "feed": feed_used,
            "status": "OK" if price is not None else "NO QUOTE",
            "resolve_diag": resolve_diag,
            "quote_diag": quote_diag,
        })

    app.disconnect()
    return rows


def fetch_ib_option(host, port, client_id, conid, market_type=1, timeout=8.0):
    if not IBAPI_AVAILABLE:
        raise RuntimeError("IB TWS Python API is niet geïnstalleerd in deze Python-omgeving.")
    app = IBOptionSnapshot()
    app.connect(host, int(port), int(client_id))
    thread = threading.Thread(target=app.run, daemon=True)
    thread.start()
    if not app.ready.wait(timeout):
        app.disconnect()
        raise RuntimeError("Geen TWS/IB Gateway verbinding. Controleer API settings, host en poort.")

    c = Contract()
    c.conId = int(conid)
    c.exchange = "EUREX"
    app.reqContractDetails(7001, c)
    if not app.details_done.wait(timeout) or not app.details:
        err = "; ".join(app.errors[-3:])
        app.disconnect()
        raise RuntimeError("Option conId niet gevonden." + (f" IB: {err}" if err else ""))

    contract = app.details[0].contract
    if contract.secType not in ("OPT", "FOP"):
        app.disconnect()
        raise RuntimeError(f"conId is {contract.secType}, geen optie.")

    app.reqMarketDataType(int(market_type))
    app.reqMktData(7002, contract, "", False, False, [])
    app.market_done.wait(timeout)
    time.sleep(0.5)
    app.cancelMktData(7002)
    app.disconnect()

    expiry_raw = contract.lastTradeDateOrContractMonth[:8]
    expiry = datetime.strptime(expiry_raw, "%Y%m%d").date() if len(expiry_raw) == 8 else None
    return {
        "conId": contract.conId,
        "symbol": contract.symbol,
        "localSymbol": contract.localSymbol,
        "expiry": expiry,
        "strike": float(contract.strike),
        "right": contract.right,
        "multiplier": float(contract.multiplier or 10),
        "exchange": contract.exchange,
        "currency": contract.currency,
        "model": app.model,
        "errors": app.errors,
    }


if "kr01_ib" not in st.session_state:
    st.session_state.kr01_ib = None
if "kr01_euribor_strip" not in st.session_state:
    st.session_state.kr01_euribor_strip = None
if "kr01_curve_key" not in st.session_state:
    st.session_state.kr01_curve_key = None

st.markdown('<div class="brand">ATLAS · RATES RISK</div>', unsafe_allow_html=True)
st.markdown('<div class="title">KR01 LAB</div><div class="sub">Echte option rate-risk via 1 bp bump-and-revalue · research only</div>', unsafe_allow_html=True)
st.markdown('<div class="lock"><span class="good">BASE V1 BLIJFT LOCKED</span><br>Dit lab verandert de bestaande hedge-engine niet. Eerst meten en valideren we de option-KR01.</div>', unsafe_allow_html=True)

st.markdown('<div class="section">1 · OPTION</div>', unsafe_allow_html=True)
source = st.radio("Input", ["MANUAL", "TWS / IB GATEWAY"], horizontal=True)

ib = st.session_state.kr01_ib
if source == "TWS / IB GATEWAY":
    c1, c2 = st.columns(2)
    host = c1.text_input("Host", "127.0.0.1")
    port = c2.number_input("Port", value=7496, step=1)
    client_id = st.number_input("Client ID", value=41, step=1)
    market_type = st.selectbox("Market data", [1, 2, 3, 4], index=0, format_func=lambda x: {1:"LIVE",2:"FROZEN",3:"DELAYED",4:"DELAYED FROZEN"}[x])

    st.caption("Zoek de optie zoals je hem in TWS ziet — conId is niet meer nodig.")
    s1, s2 = st.columns(2)
    lookup_expiry = s1.date_input("TWS option expiry", value=third_friday(2027, 9), key="tws_lookup_expiry")
    lookup_strike = s2.number_input("TWS strike", value=6300.0, step=50.0, key="tws_lookup_strike")
    lookup_right = st.selectbox("TWS Call / Put", ["C", "P"], key="tws_lookup_right")
    if st.button("FIND OPTION IN TWS", use_container_width=True):
        try:
            st.session_state.kr01_ib = find_ib_option(
                host, port, client_id, lookup_expiry, lookup_strike, lookup_right, market_type
            )
            st.rerun()
        except Exception as e:
            st.error(str(e))
    ib = st.session_state.kr01_ib
    if ib:
        st.caption(f"{ib['localSymbol']} · conId {ib['conId']} · {ib['exchange']} · {ib['currency']}")
        m = ib.get("model", {})
        if not m:
            st.warning("Contract gevonden, maar geen IB model-option data ontvangen. Controleer market-data permissies.")
        diag = ib.get("underlying_diag", {})
        if ib.get("underlying") is None:
            st.warning("ESTX50 underlying niet ontvangen. Open TWS DIAGNOSTICS hieronder; de KR01-berekening blijft geblokkeerd.")
            with st.expander("TWS DIAGNOSTICS · ESTX50", expanded=False):
                st.json(diag)
        else:
            feed = (diag.get("used_feed") or {}).get("label", "REQUESTED")
            if feed != "REQUESTED":
                st.info(f"ESTX50 {ib['underlying']:.2f} ontvangen via {feed.replace('_', ' ')} fallback. Live indexdata is niet geabonneerd.")
else:
    ib = None

valuation = st.date_input("Valuation date", value=date.today())
manual_expiry = third_friday(2027, 9)
expiry = st.date_input("Option expiry", value=(ib.get("expiry") if ib and ib.get("expiry") else manual_expiry))

c1, c2 = st.columns(2)
strike = c1.number_input("Strike", value=float(ib.get("strike", 5000.0) if ib else 5000.0), step=10.0)
right = c2.selectbox("Call / Put", ["C", "P"], index=0 if not ib or ib.get("right", "C") == "C" else 1)

model = ib.get("model", {}) if ib else {}
c1, c2 = st.columns(2)
underlying_value = ib.get("underlying") if ib else None
if underlying_value is None:
    underlying_value = model.get("undPrice")
if underlying_value is None or underlying_value <= 0:
    underlying_value = 5000.0
spot = c1.number_input("Underlying SX5E", value=float(underlying_value), step=1.0)
vol_pct = c2.number_input("Implied vol %", value=float(model.get("impliedVol", 0.20) * 100.0), step=0.1)

c1, c2 = st.columns(2)
pv_div = c1.number_input("PV dividends / index point", value=float(model.get("pvDividend", 0.0)), step=1.0)
position = c2.number_input("Position contracts", value=-1, step=1)

c1, c2 = st.columns(2)
multiplier = c1.number_input("€ multiplier / index point", value=float(ib.get("multiplier", 10.0) if ib else 10.0), step=1.0)
portfolio_rho = c2.number_input(
    "Research portfolio Rho € / +100bp (later)",
    value=-600000.0,
    step=10000.0,
    help="Dit is de aparte -600k onderzoeksportefeuille. V0.8 gebruikt dit getal NIET voor de validatie of KR01 van de ene geselecteerde optie.",
)

if ib and model:
    st.caption(
        f"IB model: option {model.get('optPrice', float('nan')):.3f} · IV {model.get('impliedVol', float('nan'))*100:.2f}% · "
        f"delta {model.get('delta', float('nan')):.4f} · PV div {model.get('pvDividend', float('nan')):.2f}. "
        "IB levert via tickOptionComputation geen bucketed rho; die rekenen we hieronder zelf."
    )

st.markdown('<div class="section">2 · EUR CURVE BUCKETS</div>', unsafe_allow_html=True)
curve_key = (valuation, expiry)
if st.session_state.kr01_curve_key != curve_key:
    st.session_state.kr01_curve_key = curve_key
    st.session_state.kr01_curve = default_curve_rows(valuation, expiry) if expiry > valuation else []

curve_df = pd.DataFrame(st.session_state.get("kr01_curve", []))
curve_edit = st.data_editor(
    curve_df,
    use_container_width=True,
    hide_index=True,
    num_rows="dynamic",
    column_config={
        "Start": st.column_config.DateColumn("Start", format="DD-MM-YY", required=True),
        "End": st.column_config.DateColumn("End", format="DD-MM-YY", required=True),
        "Forward %": st.column_config.NumberColumn("Forward %", format="%.3f", step=0.01, required=True),
    },
    key="kr01_curve_editor",
)
st.caption("Standaardgrenzen gebruiken de derde woensdag van Mar/Jun/Sep/Dec en option expiry. Pas de curve aan zodra we de definitieve EUR discount/OIS-nodes invoeren.")

st.markdown('<div class="section">3 · BUMP & REVALUE</div>', unsafe_allow_html=True)
underlying_missing = bool(source == "TWS / IB GATEWAY" and ib and ib.get("underlying") is None)
if st.button("CALCULATE BUCKETED KR01", use_container_width=True, disabled=underlying_missing):
    try:
        curve = validate_curve(curve_edit, valuation, expiry)
        st.session_state.kr01_curve = curve.to_dict("records")
        base_px, kr = bucketed_kr01(
            float(spot), float(strike), float(vol_pct) / 100.0, float(pv_div), curve,
            right, float(multiplier), int(position), bump_bp=1.0,
        )
        calc_total_bp = float(kr["KR01 €"].sum())
        calc_rho_100 = calc_total_bp * 100.0
        kr["Weight %"] = kr["KR01 €"] / calc_total_bp * 100.0 if abs(calc_total_bp) > 1e-12 else float("nan")
        kr["Single-option fut eq."] = kr["KR01 €"] / 25.0
        st.session_state.kr01_result = {
            "base_px": base_px,
            "kr": kr,
            "calc_total_bp": calc_total_bp,
            "calc_rho_100": calc_rho_100,
        }
    except Exception as e:
        st.session_state.kr01_result = None
        st.error(str(e))

r = st.session_state.get("kr01_result")
if r:
    st.markdown("#### SINGLE OPTION · VALIDATION")
    if ib and model.get("optPrice") is not None and model.get("optPrice") > 0:
        ib_px = float(model["optPrice"])
        px_gap = float(r["base_px"]) - ib_px
        px_gap_pct = abs(px_gap) / ib_px * 100.0
        p1, p2, p3 = st.columns(3)
        p1.metric("Own option px", f"{r['base_px']:.3f}")
        p2.metric("IB model px", f"{ib_px:.3f}")
        p3.metric("Price gap", f"{px_gap:+.3f}", f"{px_gap_pct:.2f}%")
        if px_gap_pct <= 1.0:
            st.success(f"GATE 2 PRICE CHECK PASS · verschil {px_gap_pct:.2f}%")
        else:
            st.warning(
                f"GATE 2 PRICE CHECK NOG NIET GESLAAGD · verschil {px_gap_pct:.2f}%. "
                "Eerst eigen curve/dividend/pricer kalibreren; portefeuille-scaling blijft uit."
            )
    else:
        st.warning("Geen geldige IB modelprijs ontvangen; Gate 2 kan nog niet worden beoordeeld.")

    a, b = st.columns(2)
    a.metric("Single-option KR01", f"€ {r['calc_total_bp']:,.2f} / bp")
    b.metric("Single-option model Rho", f"€ {r['calc_rho_100']:,.0f} / +100bp")

    display = r["kr"].copy()
    st.dataframe(
        display,
        use_container_width=True,
        hide_index=True,
        column_config={
            "KR01 €": st.column_config.NumberColumn("Own KR01 €/bp", format="€ %.2f"),
            "Weight %": st.column_config.NumberColumn("Weight", format="%.1f%%"),
            "Single-option fut eq.": st.column_config.NumberColumn("Fut eq. (1 option)", format="%.3f"),
        },
    )
    st.info(
        f"De onderzoeksportefeuille van € {portfolio_rho:,.0f} Rho staat apart en wordt hier bewust NIET gebruikt. "
        "Eerst bewijzen we de prijs en KR01-verdeling van deze ene echte OESX-optie."
    )


if r and source == "TWS / IB GATEWAY":
    st.markdown('<div class="section">4 · EURIBOR FUTURES MAPPING</div>', unsafe_allow_html=True)
    st.caption(
        "Nu koppelen we de gemeten option-KR01 aan de echte FEU3-renteperioden. "
        "Belangrijk: een contractmaand benoemt het BEGIN van de 3-maands renteperiode."
    )
    if st.button("LOAD EURIBOR FUTURES FROM TWS", use_container_width=True):
        try:
            st.session_state.kr01_euribor_strip = fetch_euribor_strip(
                host, port, int(client_id) + 1, market_type
            )
            st.rerun()
        except Exception as e:
            st.error(str(e))

    strip = st.session_state.get("kr01_euribor_strip")
    if strip:
        fut_df = pd.DataFrame([{
            "Contract": x["month"],
            "IB local": x.get("resolved_local") or "—",
            "conId": x.get("conId"),
            "Price": x["price"],
            "Implied 3M %": x["rate"],
            "Rate period": x["role"],
            "Feed": x["feed"],
            "Status": x["status"],
        } for x in strip])
        st.dataframe(
            fut_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Price": st.column_config.NumberColumn("Price", format="%.3f"),
                "Implied 3M %": st.column_config.NumberColumn("Implied 3M %", format="%.3f%%"),
            },
        )

        krdf = r["kr"].reset_index(drop=True)
        map_rows = []
        map_names = [
            ("FRONT STUB", "Geen exacte quarterly FEU3", "Oct-26 alleen proxy/overlap"),
            ("Dec-26", "IZ6", "Dec→Mar"),
            ("Mar-27", "IH7", "Mar→Jun"),
            ("Jun-27", "IM7", "Jun→Sep"),
            ("Sep-27", "IU7", "alleen laatste dagen vóór option expiry"),
        ]
        for n, row in krdf.iterrows():
            m = map_names[n] if n < len(map_names) else ("—", "—", "—")
            map_rows.append({
                "Option KR01 bucket": row["Bucket"],
                "KR01 €/bp": row["KR01 €"],
                "Weight %": row["Weight %"],
                "FEU3 mapping": m[0],
                "IB local": m[1],
                "Interpretation": m[2],
            })
        st.dataframe(
            pd.DataFrame(map_rows),
            use_container_width=True,
            hide_index=True,
            column_config={
                "KR01 €/bp": st.column_config.NumberColumn("KR01 €/bp", format="€ %.2f"),
                "Weight %": st.column_config.NumberColumn("Weight", format="%.1f%%"),
            },
        )

        ok = all(x.get("price") is not None for x in strip[1:])
        if ok:
            st.success(
                "GATE 4A INSTRUMENT MAPPING PASS · Dec-26, Mar-27, Jun-27 en Sep-27 zijn live/frozen uit TWS gekoppeld. "
                "De front stub blijft bewust apart."
            )
        else:
            st.warning("GATE 4A NOG NIET GESLAAGD · één of meer kwartaalfutures hebben geen bruikbare TWS-koers.")
            with st.expander("TWS DIAGNOSTICS · EURIBOR"):
                diag_rows = []
                for x in strip:
                    diag_rows.append({
                        "Contract": x["month"],
                        "Status": x["status"],
                        "conId": x.get("conId"),
                        "Resolve": str(x.get("resolve_diag", [])),
                        "Quote": str(x.get("quote_diag", [])),
                    })
                st.dataframe(pd.DataFrame(diag_rows), use_container_width=True, hide_index=True)
        st.warning(
            "Nog géén Gate 4B: FEU3 is 3M Euribor, terwijl de option-pricer een discount/forward curve gebruikt. "
            "Basis/convexity en de front stub moeten nog worden gevalideerd vóór Analysed Hedge."
        )

st.markdown('<div class="section">VALIDATION GATES</div>', unsafe_allow_html=True)
st.markdown(
    "**Gate 1** contract + IB modeldata · **Gate 2** eigen option price versus IB model · "
    "**Gate 3** bucketed KR01 van de optie · **Gate 4A** FEU3 instrument/period mapping · **Gate 4B** discount/OIS→Euribor basis/convexity.  "
    "De €600k onderzoeksportefeuille wordt pas daarna gekoppeld. Pas na alle gates mag `ANALYSED HEDGE` in de hoofdapp worden gevuld."
)
st.caption("ATLAS RHO · KR01 LAB V1.0 · research only")
