from datetime import date
import streamlit as st
import pandas as pd
from atlas_rho_engine import Bucket, full_result, DEFAULT_EXECUTION

st.set_page_config(page_title="ATLAS RHO", page_icon="◼", layout="centered", initial_sidebar_state="collapsed")

st.markdown("""
<style>
:root{color-scheme:dark}
.stApp{background:#0b0e13;color:#f4f6f8}
.block-container{max-width:760px;padding:1rem 1rem 5rem}
h1{font-size:2rem!important;letter-spacing:.04em;margin-bottom:0!important}
h2,h3{letter-spacing:.01em}
.atlas-sub{color:#89919e;font-size:.82rem;letter-spacing:.16em;margin:-.2rem 0 1rem}
[data-testid="stMetric"]{background:#121720;border:1px solid #252c38;border-radius:16px;padding:12px 14px;min-height:105px}
[data-testid="stMetricLabel"]{color:#8f98a7}
[data-testid="stMetricValue"]{font-size:1.55rem}
div[data-testid="stExpander"]{background:#10151c;border:1px solid #252c38;border-radius:14px}
.stButton>button{border-radius:13px;min-height:48px;font-weight:700}
div[data-baseweb="tab-list"]{gap:4px;background:#10151c;padding:5px;border-radius:14px;position:sticky;top:.25rem;z-index:10}
button[data-baseweb="tab"]{border-radius:10px}
.status{display:inline-block;padding:5px 9px;border-radius:999px;background:#17351f;color:#75d58a;font-size:.76rem;font-weight:700}
.muted{color:#89919e;font-size:.85rem}
hr{border-color:#222a35}
</style>
""", unsafe_allow_html=True)

st.title("ATLAS RHO")
st.markdown('<div class="atlas-sub">RATES RISK · MOBILE WORKSTATION</div>', unsafe_allow_html=True)

if "buckets" not in st.session_state:
    st.session_state.buckets=[{"name":"Bucket 1","expiry":date.today(),"rho":0.0}]

def load_attack():
    st.session_state.buckets=[
      {"name":"Nov26","expiry":date(2026,11,1),"rho":-100000.0},
      {"name":"Jan27","expiry":date(2027,1,1),"rho":-150000.0},
      {"name":"Jun27","expiry":date(2027,6,1),"rho":-400000.0},
      {"name":"Dec27","expiry":date(2027,12,1),"rho":500000.0},
      {"name":"Feb28","expiry":date(2028,2,1),"rho":-1250000.0},
      {"name":"Sep28","expiry":date(2028,9,1),"rho":-250000.0},
      {"name":"Dec28","expiry":date(2028,12,1),"rho":800000.0},
    ]
    st.session_state.val=date(2026,9,25)

tabs=st.tabs(["RISK","EXECUTE","CURVE","STRESS"])

with tabs[0]:
    c1,c2=st.columns([2,1])
    with c1:
        st.date_input("Valuation date",value=st.session_state.get("val",date.today()),key="val")
    with c2:
        st.write(""); st.write("")
        st.button("Load test",use_container_width=True,on_click=load_attack)

    with st.expander("Company input",expanded=True):
        n=st.number_input("Rho buckets",1,20,len(st.session_state.buckets),1)
        while len(st.session_state.buckets)<n:
            st.session_state.buckets.append({"name":f"Bucket {len(st.session_state.buckets)+1}","expiry":st.session_state.val,"rho":0.0})
        st.session_state.buckets=st.session_state.buckets[:n]
        edited=[]
        for i,b in enumerate(st.session_state.buckets):
            st.caption(f"BUCKET {i+1}")
            a,bcol=st.columns([1.05,1])
            exp=a.date_input("Expiry",value=b["expiry"],key=f"e{i}")
            rho=bcol.number_input("Rho / +100bp",value=float(b["rho"]),step=10000.0,key=f"r{i}")
            edited.append({"name":f"Bucket {i+1}","expiry":exp,"rho":rho})
        st.session_state.buckets=edited

    ass=dict(DEFAULT_EXECUTION)
    with st.expander("Execution assumptions"):
        a,b=st.columns(2)
        ass["outright_spread_bp"]=a.number_input("Outright spread bp",0.0,20.0,.5,.125)
        ass["pack_spread_bp"]=b.number_input("Pack spread bp",0.0,20.0,.5,.125)
        vals={2:.5,3:.625,4:.625,5:.625,6:.625}
        for y,v in vals.items():
            ass[f"bundle_{y}y_spread_bp"]=st.number_input(f"{y}Y Bundle spread bp",0.0,20.0,v,.125,key=f"b{y}")

    if st.button("CALCULATE / UPDATE",type="primary",use_container_width=True):
        try:
            bs=[Bucket(x["name"],x["expiry"],float(x["rho"])) for x in st.session_state.buckets]
            st.session_state.result=full_result(st.session_state.val,bs,assumptions=ass)
        except Exception as e: st.error(str(e))

    r=st.session_state.get("result")
    if r:
        a,b=st.columns(2)
        a.metric("COMPANY RHO",f"€{r['company_rho']:,.0f}")
        b.metric("DV01",f"€{r['company_dv01']:,.0f}/bp")
        a,b=st.columns(2)
        side="SHORT" if r["target_net"]<0 else "LONG" if r["target_net"]>0 else "FLAT"
        a.metric("TARGET HEDGE",f"{abs(r['target_net']):,} {side}")
        b.metric("PARALLEL RESIDUAL",f"€{r['parallel_residual_dv01']:,.1f}/bp")
        if abs(r["parallel_residual_dv01"])<25:
            st.markdown('<span class="status">PARALLEL HEDGED</span>',unsafe_allow_html=True)
        df=pd.DataFrame({"Contract":r["contracts"],"Company DV01":r["company"],"Hedge DV01":[-x*25 for x in r["target"]]})
        st.subheader("Quarterly risk")
        st.bar_chart(df.set_index("Contract"),use_container_width=True)

with tabs[1]:
    r=st.session_state.get("result")
    if not r: st.info("Calculate the company risk first.")
    else:
        side="SHORT" if r["target_net"]<0 else "LONG" if r["target_net"]>0 else "FLAT"
        a,b=st.columns(2)
        a.metric("EXACT HEDGE",f"{abs(r['target_net'])} {side}",f"€{r['exact_cost']:,.0f} est.")
        b.metric("BEST EXECUTION",f"€{r['best_cost']:,.0f}",f"Save €{r['saving']:,.0f}")
        st.subheader("Orders")
        rows=[]
        for name,stx,L,q in r["best_orders"]:
            rows.append({"Side":"BUY" if q>0 else "SELL","Qty":abs(q),"Instrument":name,"From":r["contracts"][stx],"To":r["contracts"][stx+L-1]})
        if rows: st.dataframe(rows,use_container_width=True,hide_index=True)
        with st.expander("Quarterly reconciliation"):
            audit=pd.DataFrame({"Contract":r["contracts"],"Company DV01":r["company"],"Target":r["target"],"Current":r["current"],"Trade":r["trade"]})
            st.dataframe(audit,use_container_width=True,hide_index=True)
        with st.expander("Strategy universe"):
            st.caption("Packs = 4 consecutive quarterlies. Bundles = 8 / 12 / 16 / 20 / 24 consecutive quarterlies. Best Execution is required to reconstruct the exact quarterly trade target.")

with tabs[2]:
    st.subheader("EURIBOR Curve")
    st.markdown('<div class="muted">Current curve + History + 1W / 1M / 3M / 6M will use the same historical database as desktop.</div>',unsafe_allow_html=True)
    r=st.session_state.get("result")
    if r:
        # Placeholder risk-horizon visualization until the historical CSV is added to repo.
        df=pd.DataFrame({"Contract":r["contracts"],"Quarterly DV01":r["company"]})
        st.line_chart(df.set_index("Contract"),use_container_width=True)
        st.caption("Risk horizon shown for now. Market curve history is the next data layer; no synthetic market prices are displayed.")
    else: st.info("Calculate first to establish the contract horizon.")

with tabs[3]:
    st.subheader("Stress Lab")
    r=st.session_state.get("result")
    if not r: st.info("Calculate the company risk first.")
    else:
        # First-order parallel stresses are exact from residual DV01. Curve stresses need bucket shocks.
        res=float(r["parallel_residual_dv01"])
        stress=pd.DataFrame({"Scenario":["Parallel -100","Parallel -50","Parallel +50","Parallel +100"],
                             "P&L":[res*-100,res*-50,res*50,res*100]})
        st.bar_chart(stress.set_index("Scenario"),use_container_width=True)
        st.caption("First-order DV01 stress. Front/back/steepener/flattener will be added from the desktop stress specification.")

st.markdown("---")
st.caption("ATLAS RHO · Mobile V2 · shared exact hedge engine")
