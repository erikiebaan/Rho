import math
from statistics import NormalDist
import streamlit as st

st.set_page_config(page_title="ATLAS IB Model Input Probe", layout="wide")
st.title("ATLAS · IB MODEL INPUT PROBE")
st.caption("Gate 4B-1 research only · geen hedge-aanpassing")

def call_price(spot, strike, vol, days, rate_pct, pv_div):
    t = days / 365.0
    r = rate_pct / 100.0
    df = math.exp(-r * t)
    prepaid = spot - pv_div
    forward = prepaid / df
    sig = vol / 100.0 * math.sqrt(t)
    d1 = (math.log(forward / strike) + 0.5 * (vol / 100.0) ** 2 * t) / sig
    d2 = d1 - sig
    n = NormalDist()
    return df * (forward * n.cdf(d1) - strike * n.cdf(d2))

st.markdown("### CONTROLLED INPUTS")
c1, c2, c3 = st.columns(3)
spot = c1.number_input("SX5E", value=6315.59, step=1.0)
strike = c2.number_input("Strike", value=6300.0, step=50.0)
vol = c3.number_input("IV %", value=17.143, step=0.001, format="%.3f")

c1, c2, c3 = st.columns(3)
days = c1.number_input("T · days", value=354, step=1)
rate = c2.number_input("Model Navigator R %", value=3.032, step=0.001, format="%.3f")
nav_div = c3.number_input("Model Navigator NwDvd", value=130.011, step=0.001, format="%.3f")

c1, c2, c3 = st.columns(3)
api_div = c1.number_input("API PV dividend", value=126.233, step=0.001, format="%.3f")
stream_px = c2.number_input("IB streaming model", value=453.336, step=0.001, format="%.3f")
calc_px = c3.number_input("IB calculateOptionPrice", value=450.968, step=0.001, format="%.3f")

px_nav = call_price(spot, strike, vol, days, rate, nav_div)
px_api = call_price(spot, strike, vol, days, rate, api_div)

st.markdown("### A · MODEL NAVIGATOR INPUTS")
a1, a2, a3 = st.columns(3)
a1.metric("Own price", f"{px_nav:.3f}")
a2.metric("vs streaming model", f"{px_nav-stream_px:+.3f}")
a3.metric("vs calculateOptionPrice", f"{px_nav-calc_px:+.3f}")
st.caption("SX5E + IV + T + R + NwDvd, waarbij NwDvd hier bewust als PV dividend wordt behandeld om die hypothese te testen.")

st.markdown("### B · API STREAMING INPUTS")
b1, b2, b3 = st.columns(3)
b1.metric("Own price", f"{px_api:.3f}")
b2.metric("vs streaming model", f"{px_api-stream_px:+.3f}")
b3.metric("vs calculateOptionPrice", f"{px_api-calc_px:+.3f}")
st.caption("Zelfde SX5E, IV, T en R; alleen API PV dividend vervangt NwDvd.")

gap_stream = abs(px_api-stream_px)
gap_calc = abs(px_api-calc_px)
st.markdown("### DIAGNOSIS")
if gap_calc < 0.10:
    st.success(f"B sluit vrijwel aan op calculateOptionPrice: absolute gap {gap_calc:.3f} pt.")
else:
    st.warning(f"B reproduceert calculateOptionPrice nog niet binnen 0.10 pt: absolute gap {gap_calc:.3f} pt.")
st.info(
    "Dit valideert nog geen hedge. Als B calculateOptionPrice reproduceert maar niet de streaming Model-prijs, "
    "dan zit het resterende verschil in de streaming Model-context/conventie en niet in deze eenvoudige R + PV-dividend combinatie."
)
