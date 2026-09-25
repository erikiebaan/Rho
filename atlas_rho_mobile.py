from datetime import date
import html
import pandas as pd
import streamlit as st
from atlas_rho_engine import Bucket, full_result, DEFAULT_EXECUTION
from atlas_rho_stress import stress_results
from atlas_rho_history import available_dates, comparison

def normalize_history(df):
    x=df.copy()
    cols={str(c).strip().lower():c for c in x.columns}
    d=next((cols[k] for k in ("date","trade_date","day") if k in cols),None)
    p=next((cols[k] for k in ("close","price","last","settle","settlement") if k in cols),None)
    m=next((cols[k] for k in ("requested_expiry","contract","expiry","maturity","local_symbol") if k in cols),None)
    if d is None or p is None or m is None:
        raise ValueError(f"History columns not recognised: {list(x.columns)}")
    x=x[[d,m,p]].rename(columns={d:"date",m:"contract",p:"price"})
    x["date"]=pd.to_datetime(x["date"],errors="coerce").dt.normalize()
    x["price"]=pd.to_numeric(x["price"],errors="coerce")
    x=x.dropna().drop_duplicates(["date","contract"],keep="last").sort_values(["date","contract"])
    x["implied_rate"]=100.0-x["price"]
    return x

st.set_page_config(page_title="ATLAS RHO",page_icon="◼",layout="centered",initial_sidebar_state="collapsed")

ACCENT="#d8ff32"
st.markdown(f"""
<style>
:root{{color-scheme:dark}}
.stApp{{background:#080a0d;color:#f7f8fa}}
.block-container{{max-width:720px;padding:.7rem 1rem 5.5rem}}
header[data-testid="stHeader"]{{background:transparent}}
h1,h2,h3,p{{font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display",Inter,sans-serif}}
#MainMenu,footer{{visibility:hidden}}\n[data-testid='stToolbar']{{visibility:hidden;height:0}}\n[data-testid='stDecoration']{{display:none}}
.hero{{padding:.5rem .1rem .9rem}}
.brand{{font-size:.72rem;letter-spacing:.24em;color:#8b929e;font-weight:700}}
.title{{font-size:2.05rem;font-weight:650;letter-spacing:-.035em;margin:.2rem 0}}
.live{{display:inline-flex;align-items:center;gap:6px;font-size:.7rem;color:#aeb5bf}}
.dot{{width:7px;height:7px;border-radius:50%;background:{ACCENT};display:inline-block}}
.kgrid{{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin:.4rem 0 1rem}}
.kpi{{background:linear-gradient(145deg,#15191e,#101318);border:1px solid #252a31;border-radius:19px;padding:13px 14px;min-height:82px}}
.kl{{font-size:.65rem;letter-spacing:.11em;color:#7e8794;font-weight:700}}
.kv{{font-size:1.35rem;color:#f6f7f8;font-weight:600;margin-top:8px;letter-spacing:-.03em}}
.good{{color:{ACCENT}}}
.section{{font-size:.72rem;letter-spacing:.13em;color:#7e8794;font-weight:700;margin:1.2rem 0 .55rem}}
.rowcard{{display:flex;align-items:center;justify-content:space-between;background:#111419;border:1px solid #22272f;border-radius:13px;padding:8px 12px;margin:5px 0}}
.rowmain{{font-size:.94rem;color:#f2f4f7;font-weight:600}} .rowsub{{font-size:.7rem;color:#747d89;margin-top:3px}}
.num{{font-size:1rem;font-weight:650}}
.order{{background:#111419;border:1px solid #22272f;border-radius:17px;padding:13px 15px;margin:8px 0}}
.orderhead{{display:flex;justify-content:space-between;align-items:center}}
.side{{font-size:.72rem;color:{ACCENT};font-weight:800;letter-spacing:.08em}}
.qty{{font-size:1.12rem;font-weight:650}} .route{{font-size:.73rem;color:#7f8792;margin-top:5px}}
.stButton>button{{border-radius:14px;min-height:46px;font-weight:700;border:1px solid #292f38;background:#171b21;color:#f5f6f7}}
.stButton>button[kind="primary"]{{background:{ACCENT};color:#090b0e;border:0}}
div[data-baseweb="tab-list"]{{gap:2px;background:#0d1014;padding:4px;border-radius:15px;position:sticky;top:.25rem;z-index:10}}
button[data-baseweb="tab"]{{border-radius:11px;font-size:.77rem;padding:.45rem .65rem;color:#7e8794!important}}\nbutton[data-baseweb="tab"][aria-selected="true"]{{color:#d8ff32!important}}\ndiv[data-baseweb="tab-highlight"]{{background-color:#d8ff32!important}}\nbutton[data-baseweb='tab'][aria-selected='true'] p{{color:#d8ff32!important}}\n[data-testid='stFileUploaderDropzone']{{background:#111419!important;border:1px solid #252a31!important;border-radius:17px!important}}\n[data-testid='stFileUploaderDropzone'] button{{background:#171b21!important;color:#f5f6f7!important;border:1px solid #292f38!important}}\n[data-testid='stAlert']{{background:#111419!important;border:1px solid #252a31!important;color:#aeb5bf!important}}
div[data-testid="stExpander"]{{background:#0f1217;border:1px solid #232832;border-radius:15px}}
[data-testid="stMetric"]{{background:#12161c;border:1px solid #252b34;border-radius:17px;padding:12px}}
[data-testid="stMetricValue"]{{color:#f5f7f9!important}}
[data-testid="stMetricLabel"]{{color:#8d96a3!important}}
[data-testid="stDataFrame"]{{border-radius:14px;overflow:hidden}}
.muted{{font-size:.78rem;color:#78818d}}
hr{{border-color:#20252d}}
</style>""",unsafe_allow_html=True)

def money_short(x):
    s="−" if x<0 else "+" if x>0 else ""
    a=abs(x)
    if a>=1_000_000:return f"{s}€{a/1_000_000:.2f}m"
    if a>=1000:return f"{s}€{a/1000:.1f}k"
    return f"{s}€{a:,.0f}"

def reset_widget_keys():
    for k in list(st.session_state.keys()):
        if k.startswith("e_") or k.startswith("r_"): del st.session_state[k]

def load_test():
    st.session_state.val=date(2026,9,25)
    st.session_state.buckets=[
      {"name":"Nov 26","expiry":date(2026,11,1),"rho":-100000.0},
      {"name":"Jan 27","expiry":date(2027,1,1),"rho":-150000.0},
      {"name":"Jun 27","expiry":date(2027,6,1),"rho":-400000.0},
      {"name":"Dec 27","expiry":date(2027,12,1),"rho":500000.0},
      {"name":"Feb 28","expiry":date(2028,2,1),"rho":-1250000.0},
      {"name":"Sep 28","expiry":date(2028,9,1),"rho":-250000.0},
      {"name":"Dec 28","expiry":date(2028,12,1),"rho":800000.0}]
    reset_widget_keys()
    st.session_state.result=None

if "val" not in st.session_state: st.session_state.val=date.today()
if "buckets" not in st.session_state: st.session_state.buckets=[]
if "result" not in st.session_state: st.session_state.result=None

r=st.session_state.result
st.markdown('<div class="hero"><div class="brand">ATLAS · RATES RISK</div><div class="title">RHO</div><div class="live"><span class="dot"></span>MOBILE WORKSTATION</div></div>',unsafe_allow_html=True)

if r:
    side="S" if r["target_net"]<0 else "L" if r["target_net"]>0 else "—"
    resid=0.0 if abs(r["parallel_residual_dv01"])<1e-8 else r["parallel_residual_dv01"]
    st.markdown(f'''<div class="kgrid">
    <div class="kpi"><div class="kl">COMPANY RHO</div><div class="kv">{money_short(r["company_rho"])}</div></div>
    <div class="kpi"><div class="kl">DV01</div><div class="kv">{money_short(r["company_dv01"])}/bp</div></div>
    <div class="kpi"><div class="kl">HEDGE</div><div class="kv">{abs(r["target_net"])} {side}</div></div>
    <div class="kpi"><div class="kl">RESIDUAL</div><div class="kv good">€{resid:,.0f}/bp</div></div></div>''',unsafe_allow_html=True)
else:
    st.markdown('''<div class="kgrid"><div class="kpi"><div class="kl">COMPANY RHO</div><div class="kv">—</div></div><div class="kpi"><div class="kl">DV01</div><div class="kv">—</div></div><div class="kpi"><div class="kl">HEDGE</div><div class="kv">—</div></div><div class="kpi"><div class="kl">RESIDUAL</div><div class="kv">—</div></div></div>''',unsafe_allow_html=True)

risk,execute,curve,stress=st.tabs(["RISK","EXECUTE","CURVE","STRESS"])

with risk:
    a,b=st.columns([1.45,1])
    a.date_input("Valuation",key="val")
    b.button("LOAD TEST",use_container_width=True,on_click=load_test)

    st.markdown('<div class="section">COMPANY RHO BUCKETS</div>',unsafe_allow_html=True)
    if not st.session_state.buckets:
        st.markdown('<div class="muted">No rho buckets yet. Add one below or load the audited test case.</div>',unsafe_allow_html=True)
    for i,x in enumerate(st.session_state.buckets):
        st.markdown(f'<div class="rowcard"><div><div class="rowmain">{html.escape(x["expiry"].strftime("%b %y").upper())}</div></div><div class="num">{money_short(x["rho"])}</div></div>',unsafe_allow_html=True)

    with st.expander("＋  ADD / EDIT BUCKETS",expanded=False):
        n=st.number_input("Number of buckets",1,20,max(1,len(st.session_state.buckets)),1,key="bucket_n")
        base=list(st.session_state.buckets)
        while len(base)<n: base.append({"name":f"Bucket {len(base)+1}","expiry":st.session_state.val,"rho":0.0})
        base=base[:n]; edited=[]
        for i,x in enumerate(base):
            c1,c2=st.columns([1,1])
            exp=c1.date_input(f"Expiry {i+1}",value=x["expiry"],key=f"e_{i}")
            rho=c2.number_input(f"Rho {i+1}",value=float(x["rho"]),step=10000.0,key=f"r_{i}")
            edited.append({"name":f"Bucket {i+1}","expiry":exp,"rho":rho})
        if st.button("SAVE BUCKETS",use_container_width=True):
            st.session_state.buckets=edited; st.session_state.result=None; st.rerun()

    with st.expander("⚙  EXECUTION ASSUMPTIONS"):
        ass=dict(DEFAULT_EXECUTION)
        c1,c2=st.columns(2)
        ass["outright_spread_bp"]=c1.number_input("Outright bp",0.0,20.0,.5,.125)
        ass["pack_spread_bp"]=c2.number_input("Pack bp",0.0,20.0,.5,.125)
        for y,v in {2:.5,3:.625,4:.625,5:.625,6:.625}.items():
            ass[f"bundle_{y}y_spread_bp"]=st.number_input(f"{y}Y Bundle bp",0.0,20.0,v,.125,key=f"bundle_{y}")
    if st.button("CALCULATE EXACT HEDGE",type="primary",use_container_width=True):
        if not st.session_state.buckets: st.warning("Add at least one rho bucket.")
        else:
            bs=[Bucket(x["name"],x["expiry"],float(x["rho"])) for x in st.session_state.buckets]
            st.session_state.result=full_result(st.session_state.val,bs,assumptions=ass); st.rerun()

    if r:
        df=pd.DataFrame({"Contract":r["contracts"],"Company":r["company"],"Hedge":[-q*25 for q in r["target"]]})
        st.markdown('<div class="section">QUARTERLY DV01</div>',unsafe_allow_html=True)
        st.bar_chart(df.set_index("Contract"),use_container_width=True)

with execute:
    if not r: st.info("Calculate the company risk first.")
    else:
        st.markdown(f'<div class="section">BEST EXECUTION</div><div class="kgrid"><div class="kpi"><div class="kl">EST. COST</div><div class="kv">€{r["best_cost"]:,.0f}</div></div><div class="kpi"><div class="kl">SAVING</div><div class="kv good">€{r["saving"]:,.0f}</div></div></div>',unsafe_allow_html=True)
        if r["best_orders"]:
            for name,stx,L,q in r["best_orders"]:
                side="BUY" if q>0 else "SELL"; route=r["contracts"][stx] if L==1 else f'{r["contracts"][stx]} → {r["contracts"][stx+L-1]}'
                st.markdown(f'<div class="order"><div class="orderhead"><span class="side">{side}</span><span class="qty">{abs(q)} · {html.escape(name.upper())}</span></div><div class="route">{html.escape(route)}</div></div>',unsafe_allow_html=True)
        else: st.success("No trade required.")
        with st.expander("QUARTERLY RECONCILIATION"):
            st.dataframe(pd.DataFrame({"Contract":r["contracts"],"Target":r["target"],"Trade":r["trade"]}),use_container_width=True,hide_index=True)

with curve:
    st.subheader('EURIBOR CURVE')
    mode=st.selectbox('CURVE VIEW',['NOW','HISTORY'],key='curve_view')
    if mode=='NOW':
        st.caption('NOW = latest available Euribor futures curve from the built-in ATLAS database.')
        cu=None
        try:
            _h=normalize_history(pd.read_csv('atlas_euribor_history.csv'))
            _d=available_dates(_h)[-1]
            _z=_h[_h['date']==_d][['contract','price']].rename(columns={'price':'close'})
            cu=_z
        except Exception:
            pass
        if cu is None:
            st.info('Load the latest Euribor curve to show NOW. The screen will use the most recent available market date.')
        else:
            try:
                cdf=cu.copy() if isinstance(cu,pd.DataFrame) else pd.read_csv(cu)
                cmap={str(c).strip().lower():c for c in cdf.columns}
                pcol=next((cmap[k] for k in ('close','price','last','settle','settlement') if k in cmap),None)
                xcol=next((cmap[k] for k in ('contract','expiry','maturity') if k in cmap),None)
                if pcol is None or xcol is None:
                    st.error('CSV needs contract/expiry and price/close/last/settle columns.')
                else:
                    plot=cdf[[xcol,pcol]].copy()
                    plot[pcol]=pd.to_numeric(plot[pcol],errors='coerce')
                    plot=plot.dropna(subset=[pcol]).drop_duplicates(subset=[xcol],keep='last')
                    plot['Implied rate %']=100.0-plot[pcol]
                    if plot.empty:
                        st.error('No valid futures prices found.')
                    else:
                        front=float(plot['Implied rate %'].iloc[0]); back=float(plot['Implied rate %'].iloc[-1]); slope=(back-front)*100.0
                        shape='UPWARD' if slope>5 else 'DOWNWARD' if slope<-5 else 'FLAT'
                        c1,c2=st.columns(2); c1.metric('Front rate',f'{front:.3f}%'); c2.metric('Curve shape',shape,f'{slope:+.1f} bp')
                        st.line_chart(plot[[xcol,'Implied rate %']].set_index(xcol),use_container_width=True)
                        with st.expander('CURVE TABLE'): st.dataframe(plot[[xcol,pcol,'Implied rate %']],use_container_width=True,hide_index=True)
                        st.caption('Implied rate = 100 - futures price. Descriptive curve shape only; not an ECB forecast.')
            except Exception as e: st.error(f'Could not read curve CSV: {e}')
    else:
        st.caption('Choose a historical date and compare the realized curve after 1 week, 1 month, 3 months and 6 months.')
        try:
            hu=pd.read_csv('atlas_euribor_history.csv')
        except Exception:
            hu=None
        if hu is None:
            st.error('Built-in history database not found.')
        else:
            try:
                hist=normalize_history(hu); dates=available_dates(hist)
                if not dates: st.error('No valid history dates found.')
                else:
                    hd=st.date_input('Historical date',value=dates[-1].date(),min_value=dates[0].date(),max_value=dates[-1].date(),key='history_date')
                    comp=comparison(hist,hd)
                    wide=None; labels=[]
                    for label,z in comp.items():
                        d=z['actual_date'].iloc[0].date(); labels.append(f'{label} · {d}')
                        one=z[['contract','implied_rate']].rename(columns={'implied_rate':label}).set_index('contract')
                        wide=one if wide is None else wide.join(one,how='outer')
                    st.caption('  |  '.join(labels))
                    st.line_chart(wide,use_container_width=True)
                    with st.expander('HISTORY TABLE'): st.dataframe(wide.reset_index(),use_container_width=True,hide_index=True)
                    st.caption('Forward horizons use 5 / 21 / 63 / 126 available trading days. This shows realized history, not a forecast.')
            except Exception as e:
                try:
                    _dbg=pd.read_csv('atlas_euribor_history.csv',nrows=2)
                    st.error(f"History debug | columns={list(_dbg.columns)} | error={type(e).__name__}: {e}")
                except Exception as _e:
                    st.error(f"History file debug failed: {type(_e).__name__}: {_e}")

with stress:
    st.markdown('<div class="section">STRESS LAB</div>',unsafe_allow_html=True)
    if not r: st.info("Calculate the company risk first.")
    else:
        res=0.0 if abs(r["parallel_residual_dv01"])<1e-8 else float(r["parallel_residual_dv01"])
        vals=[res*x for x in (-100,-50,50,100)]
        if all(abs(x)<.005 for x in vals):
            st.markdown('<div class="kpi"><div class="kl">PARALLEL STRESS</div><div class="kv good">€0</div><div class="rowsub">Exact parallel hedge · ±50 / ±100bp</div></div>',unsafe_allow_html=True)
        else:
            sdf=pd.DataFrame({"Scenario":["−100bp","−50bp","+50bp","+100bp"],"P&L":vals})
            st.dataframe(sdf,use_container_width=True,hide_index=True)
        sr=stress_results(r["company"],r["target"])
        sdf=pd.DataFrame({"Scenario":list(sr["scenarios"].keys()),"P&L":list(sr["scenarios"].values())})
        st.bar_chart(sdf.set_index("Scenario"),use_container_width=True)
        with st.expander("SCENARIO DEFINITIONS"):
            st.caption("First-order residual DV01 after hedge. Parallel +/-50 and +/-100bp, Front +50, Back +50, Bear steepener and Bull flattener.")

st.markdown("---")
st.caption("ATLAS RHO · Mobile V4.3 · exact hedge engine")
