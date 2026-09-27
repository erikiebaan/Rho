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
        self.details = []
        self.model = {}
        self.prices = {}
        self.errors = []

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
        if price is not None and price > 0:
            self.prices[tickType] = float(price)

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
    c3, c4 = st.columns(2)
    client_id = c3.number_input("Client ID", value=41, step=1)
    conid = c4.number_input("Option conId", value=0, step=1)
    market_type = st.selectbox("Market data", [1, 2, 3, 4], index=0, format_func=lambda x: {1:"LIVE",2:"FROZEN",3:"DELAYED",4:"DELAYED FROZEN"}[x])
    if st.button("GET OPTION FROM TWS", use_container_width=True):
        try:
            st.session_state.kr01_ib = fetch_ib_option(host, port, client_id, int(conid), market_type)
            st.rerun()
        except Exception as e:
            st.error(str(e))
    ib = st.session_state.kr01_ib
    if ib:
        st.caption(f"{ib['localSymbol']} · conId {ib['conId']} · {ib['exchange']} · {ib['currency']}")
        m = ib.get("model", {})
        if not m:
            st.warning("Contract gevonden, maar geen IB model-option data ontvangen. Controleer market-data permissies.")
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
spot = c1.number_input("Underlying SX5E", value=float(model.get("undPrice", 5000.0)), step=1.0)
vol_pct = c2.number_input("Implied vol %", value=float(model.get("impliedVol", 0.20) * 100.0), step=0.1)

c1, c2 = st.columns(2)
pv_div = c1.number_input("PV dividends / index point", value=float(model.get("pvDividend", 0.0)), step=1.0)
position = c2.number_input("Position contracts", value=-1, step=1)

c1, c2 = st.columns(2)
multiplier = c1.number_input("€ multiplier / index point", value=float(ib.get("multiplier", 10.0) if ib else 10.0), step=1.0)
source_rho = c2.number_input("Source total Rho € / +100bp", value=-600000.0, step=10000.0)

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
if st.button("CALCULATE BUCKETED KR01", use_container_width=True):
    try:
        curve = validate_curve(curve_edit, valuation, expiry)
        st.session_state.kr01_curve = curve.to_dict("records")
        base_px, kr = bucketed_kr01(
            float(spot), float(strike), float(vol_pct) / 100.0, float(pv_div), curve,
            right, float(multiplier), int(position), bump_bp=1.0,
        )
        calc_total_bp = float(kr["KR01 €"].sum())
        calc_rho_100 = calc_total_bp * 100.0
        target_bp = float(source_rho) / 100.0
        scale = target_bp / calc_total_bp if abs(calc_total_bp) > 1e-12 else float("nan")
        kr["Weight %"] = kr["KR01 €"] / calc_total_bp * 100.0 if abs(calc_total_bp) > 1e-12 else float("nan")
        kr["Scaled KR01 €"] = kr["KR01 €"] * scale
        kr["Direct futures eq."] = kr["Scaled KR01 €"] / 25.0
        st.session_state.kr01_result = {
            "base_px": base_px,
            "kr": kr,
            "calc_total_bp": calc_total_bp,
            "calc_rho_100": calc_rho_100,
            "target_bp": target_bp,
            "scale": scale,
        }
    except Exception as e:
        st.session_state.kr01_result = None
        st.error(str(e))

r = st.session_state.get("kr01_result")
if r:
    a, b, c = st.columns(3)
    a.metric("Own model Rho", f"€ {r['calc_rho_100']:,.0f}")
    b.metric("Source Rho", f"€ {source_rho:,.0f}")
    gap = r["calc_rho_100"] - float(source_rho)
    c.metric("Rho gap", f"€ {gap:,.0f}")
    if ib and model.get("optPrice") is not None:
        p1, p2, p3 = st.columns(3)
        p1.metric("Own option px", f"{r['base_px']:.3f}")
        p2.metric("IB model px", f"{model['optPrice']:.3f}")
        p3.metric("Price gap", f"{r['base_px']-model['optPrice']:+.3f}")

    display = r["kr"].copy()
    st.dataframe(
        display,
        use_container_width=True,
        hide_index=True,
        column_config={
            "KR01 €": st.column_config.NumberColumn("Own KR01 €/bp", format="€ %.2f"),
            "Weight %": st.column_config.NumberColumn("Weight", format="%.1f%%"),
            "Scaled KR01 €": st.column_config.NumberColumn("Scaled to source", format="€ %.2f"),
            "Direct futures eq.": st.column_config.NumberColumn("Fut eq.", format="%.1f"),
        },
    )

    own = r["calc_rho_100"]
    src = float(source_rho)
    rel_gap = abs(own - src) / max(abs(src), 1.0)
    if rel_gap <= 0.05:
        st.success(f"TOTAL-RHO CHECK PASS · afwijking {rel_gap*100:.1f}%")
    else:
        st.warning(f"TOTAL-RHO CHECK NIET GESLAAGD · afwijking {rel_gap*100:.1f}%. Eerst curve/dividend/model kalibreren; analysed hedge blijft uit.")

    st.caption(
        "Scaled KR01 behoudt de door bump-and-revalue gemeten bucketgewichten, maar schaalt de som exact naar de opgegeven source Rho. "
        "Direct futures eq. is alleen een 25 €/bp equivalent per bucket — nog géén gevalideerde FEU3 mapping of Analysed Hedge."
    )

st.markdown('<div class="section">VALIDATION GATES</div>', unsafe_allow_html=True)
st.markdown(
    "**Gate 1** contract + IB modeldata · **Gate 2** eigen option price versus markt/model · "
    "**Gate 3** som bucketed KR01 versus source total Rho · **Gate 4** OIS→Euribor mapping.  "
    "Pas na alle vier gates mag `ANALYSED HEDGE` in de hoofdapp worden gevuld."
)
st.caption("ATLAS RHO · KR01 LAB V0.1 · research only")
