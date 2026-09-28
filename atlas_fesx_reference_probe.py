import threading
import time
import pandas as pd
import streamlit as st
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract

st.set_page_config(page_title="ATLAS FESX Reference Probe", layout="wide")
st.title("ATLAS · FESX REFERENCE PROBE")
st.caption("Research only · leest TWS market data · plaatst geen orders")

class App(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.details = {}
        self.done = {}
        self.prices = {}
        self.errors = []

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        if errorCode not in (2104, 2106, 2158):
            self.errors.append((reqId, errorCode, errorString))

    def contractDetails(self, reqId, contractDetails):
        self.details.setdefault(reqId, []).append(contractDetails)

    def contractDetailsEnd(self, reqId):
        self.done.setdefault(reqId, threading.Event()).set()

    def tickPrice(self, reqId, tickType, price, attrib):
        if price and price > 0:
            self.prices.setdefault(reqId, {})[tickType] = float(price)

def best_price(t):
    for k in (4, 68, 9, 75):
        if k in t and t[k] > 0:
            return t[k]
    bids = [t.get(k) for k in (1, 66) if t.get(k, 0) > 0]
    asks = [t.get(k) for k in (2, 67) if t.get(k, 0) > 0]
    if bids and asks:
        return (bids[0] + asks[0]) / 2
    return None

def contract(symbol, sec_type, exchange, currency, month=""):
    c = Contract()
    c.symbol = symbol
    c.secType = sec_type
    c.exchange = exchange
    c.currency = currency
    if month:
        c.lastTradeDateOrContractMonth = month
    return c

def resolve(app, req_id, c):
    app.done[req_id] = threading.Event()
    app.reqContractDetails(req_id, c)
    app.done[req_id].wait(6)
    ds = app.details.get(req_id, [])
    return ds

def quote(app, req_id, c):
    for md, label in ((1,"LIVE"),(2,"FROZEN"),(4,"DELAYED FROZEN"),(3,"DELAYED")):
        app.reqMarketDataType(md)
        app.prices.pop(req_id, None)
        app.reqMktData(req_id, c, "", False, False, [])
        time.sleep(1.2)
        app.cancelMktData(req_id)
        p = best_price(app.prices.get(req_id, {}))
        if p is not None:
            return p, label
    return None, "NO PRICE"

host = st.text_input("Host", "127.0.0.1")
port = st.number_input("Port", value=7496, step=1)
client_id = st.number_input("Client ID", value=61, step=1)
sx5e = st.number_input("SX5E comparison level", value=6315.59, step=1.0)

if st.button("READ JUN / SEP / DEC 2027 FROM TWS", use_container_width=True):
    app = App()
    app.connect(host, int(port), int(client_id))
    th = threading.Thread(target=app.run, daemon=True)
    th.start()
    time.sleep(1.0)
    rows = []
    for n, month in enumerate(("202706","202709","202712")):
        ds = resolve(app, 100+n, contract("ESTX50","FUT","EUREX","EUR",month))
        exact = [d for d in ds if (getattr(d,"contractMonth","") or "").startswith(month)]
        fesx = [d for d in exact if (d.contract.tradingClass or "") == "FESX"]
        d = fesx[0] if fesx else (exact[0] if exact else (ds[0] if ds else None))
        if d is None:
            rows.append({"Month":month,"Contract":"NOT FOUND","Price":None,"FESX - SX5E":None,"Feed":"—"})
            continue
        p, feed = quote(app, 200+n, d.contract)
        rows.append({
            "Month": month,
            "Contract": d.contract.localSymbol,
            "ConId": d.contract.conId,
            "Price": p,
            "FESX - SX5E": None if p is None else p-float(sx5e),
            "Feed": feed,
        })
    app.disconnect()
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.caption("Diagnostiek: dit kiest nog geen reference contract. We meten alleen de drie FESX-kandidaten rond de OESX Sep-27 expiry.")
    if app.errors:
        with st.expander("IB messages"):
            st.write(app.errors)
