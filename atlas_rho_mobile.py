from datetime import date
import io
import re
import html
import pandas as pd
import streamlit as st
import altair as alt
from atlas_rho_engine import Bucket, full_result, DEFAULT_EXECUTION
from atlas_rho_stress import stress_results
from atlas_rho_history import available_dates

def parse_rho_paste(raw):
    months={"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,"jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12,
            "mei":5,"okt":10}
    out=[]; bad=[]
    for line in raw.splitlines():
        line=line.strip()
        if not line: continue
        m=re.match(r"^([A-Za-z]{3})[- /](\d{2,4})\s+([+\-]?[\d.,]+)\s*$",line)
        if not m or m.group(1).lower() not in months:
            bad.append(line); continue
        mon=months[m.group(1).lower()]
        y=int(m.group(2)); y=2000+y if y<100 else y
        num=m.group(3).replace(" ","")
        if "," in num and "." in num: num=num.replace(".","").replace(",",".")
        elif "," in num: num=num.replace(",",".")
        try: rho=float(num)
        except ValueError:
            bad.append(line); continue
        out.append({"name":f"{m.group(1).title()} {str(y)[-2:]}","expiry":date(y,mon,1),"rho":rho,"hedge":True})
    return out,bad

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



def contract_label(v):
    z=str(v).strip()
    m=re.fullmatch(r"(\\d{4})(\\d{2})",z)
    if m:
        try: return pd.Timestamp(int(m.group(1)),int(m.group(2)),1).strftime("%b-%y")
        except Exception: return z
    try: return pd.to_datetime(z).strftime("%b-%y")
    except Exception: return z


def curve_compare_chart(wide, visible):
    z=wide.reset_index().melt(id_vars="contract",value_vars=visible,var_name="Horizon",value_name="Rate")
    z=z.dropna(subset=["Rate"])
    order=list(wide.index)
    domain=["NOW","+1W","+1M","+3M","+6M"]
    colors=["#d8ff32","#79b8ff","#3f86ff","#ff6b6b","#ffb0b0"]
    ymin=float(z["Rate"].min()); ymax=float(z["Rate"].max())
    pad=max((ymax-ymin)*0.22,0.08)
    line=alt.Chart(z).mark_line(point=True).encode(
        x=alt.X("contract:N",sort=order,title=None,axis=alt.Axis(labelAngle=-45)),
        y=alt.Y("Rate:Q",title="Implied rate %",scale=alt.Scale(domain=[ymin-pad,ymax+pad],zero=False)),
        color=alt.Color("Horizon:N",scale=alt.Scale(domain=domain,range=colors),legend=None),
        strokeWidth=alt.condition(alt.datum.Horizon=="NOW",alt.value(4),alt.value(2.2)),
        tooltip=["contract:N","Horizon:N",alt.Tooltip("Rate:Q",format=".3f")]
    )
    last=z.groupby("Horizon",as_index=False).tail(1)
    labels=alt.Chart(last).mark_text(align="left",dx=8,fontSize=12,fontWeight="bold").encode(
        x=alt.X("contract:N",sort=order),y="Rate:Q",
        text="Horizon:N",color=alt.Color("Horizon:N",scale=alt.Scale(domain=domain,range=colors),legend=None))
    return (line+labels).properties(height=330,background="#080a0d").configure_view(strokeOpacity=0,fill="#080a0d").configure_axis(
        gridColor="#252b33",domainColor="#4a515c",tickColor="#4a515c",labelColor="#aeb5bf",titleColor="#aeb5bf"
    )

def dark_line_chart(df, xcol, ycols, height=330):
    z=df[[xcol]+ycols].melt(id_vars=xcol,var_name="Series",value_name="Value").dropna()
    ymin=float(z["Value"].min()); ymax=float(z["Value"].max())
    pad=max((ymax-ymin)*0.18,0.08)
    domain=["NOW","+1W","+1M","+3M","+6M","Implied rate %"]
    colors=["#d8ff32","#79b8ff","#3f86ff","#ff6b6b","#ffb0b0","#d8ff32"]
    return alt.Chart(z).mark_line(point=True,strokeWidth=3).encode(
        x=alt.X(f"{xcol}:N",sort=list(df[xcol]),title=None,axis=alt.Axis(labelAngle=-45)),
        y=alt.Y("Value:Q",title="Implied rate %",scale=alt.Scale(domain=[ymin-pad,ymax+pad],zero=False)),
        color=alt.Color("Series:N",scale=alt.Scale(domain=domain,range=colors),legend=None),
        tooltip=[alt.Tooltip(f"{xcol}:N"),"Series:N",alt.Tooltip("Value:Q",format=".3f")]
    ).properties(height=height,background="#080a0d").configure_view(strokeOpacity=0,fill="#080a0d").configure_axis(
        gridColor="#252b33",domainColor="#4a515c",tickColor="#4a515c",
        labelColor="#aeb5bf",titleColor="#aeb5bf"
    )

def dark_stress_chart(df):
    z=df.copy()
    ymin=float(z["P&L"].min()); ymax=float(z["P&L"].max())
    span=max(ymax-ymin,abs(ymin),abs(ymax),1.0)
    pad=span*.15
    return alt.Chart(z).mark_bar(cornerRadiusTopLeft=4,cornerRadiusTopRight=4).encode(
        x=alt.X("Scenario:N",sort=None,title=None,axis=alt.Axis(labelAngle=-45)),
        y=alt.Y("P&L:Q",title="P&L €",scale=alt.Scale(domain=[min(0,ymin)-pad,max(0,ymax)+pad],zero=True)),
        color=alt.condition(alt.datum["P&L"]>=0,alt.value("#d8ff32"),alt.value("#ff5d62")),
        tooltip=["Scenario:N",alt.Tooltip("P&L:Q",format=",.0f",title="P&L €")]
    ).properties(height=300,background="#080a0d").configure_view(strokeOpacity=0,fill="#080a0d").configure_axis(
        gridColor="#252b33",domainColor="#4a515c",tickColor="#4a515c",
        labelColor="#aeb5bf",titleColor="#aeb5bf"
    )

def history_comparison(df, selected_date):
    dates=available_dates(df)
    if not dates: return {}
    target=pd.Timestamp(selected_date).normalize()
    base=min(dates,key=lambda d:abs((d-target).days))
    i=dates.index(base)
    out={}
    for label,offset in {"NOW":0,"+1W":5,"+1M":21,"+3M":63,"+6M":126}.items():
        j=i+offset
        if j>=len(dates): continue
        d=dates[j]
        z=df[df["date"]==d][["contract","implied_rate"]].copy()
        z["actual_date"]=d
        out[label]=z
    return out

def hedge_matrix(valuation,buckets):
    if not buckets: return pd.DataFrame()
    all_bs=[Bucket(x["name"],x["expiry"],float(x["rho"])) for x in buckets]
    all_r=full_result(valuation,all_bs)
    contracts=all_r["contracts"]
    rows=[]
    for x in buckets:
        one=full_result(valuation,[Bucket(x["name"],x["expiry"],float(x["rho"]))])
        mp=dict(zip(one["contracts"],one["company"]))
        row={"Rho month":x["expiry"].strftime("%b-%y"),"Rho €":float(x["rho"]),"Hedge":bool(x.get("hedge",True))}
        for c in contracts: row[c]=float(mp.get(c,0.0))
        rows.append(row)
    return pd.DataFrame(rows)

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
        if k.startswith("e_") or k.startswith("r_") or k.startswith("h_"): del st.session_state[k]

def execution_assumptions_from_state():
    a=dict(DEFAULT_EXECUTION)
    a["outright_spread_bp"]=float(st.session_state.get("ass_out",.5))
    a["pack_spread_bp"]=float(st.session_state.get("ass_pack",.5))
    for y,v in {2:.5,3:.625,4:.625,5:.625,6:.625}.items():
        a[f"bundle_{y}y_spread_bp"]=float(st.session_state.get(f"bundle_{y}",v))
    return a

def input_signature():
    b=tuple((str(x.get("expiry")),float(x.get("rho",0.0)),bool(x.get("hedge",True))) for x in st.session_state.get("buckets",[]))
    a=execution_assumptions_from_state()
    return (str(st.session_state.get("val")),b,tuple(sorted(a.items())))

def invalidate_if_inputs_changed():
    if st.session_state.get("result") is not None and st.session_state.get("result_signature") != input_signature():
        st.session_state.result=None
        st.session_state.result_signature=None

def load_test():
    st.session_state.val=date(2026,9,25)
    st.session_state.buckets=[
      {"name":"Nov 26","expiry":date(2026,11,1),"rho":-100000.0,"hedge":True},
      {"name":"Jan 27","expiry":date(2027,1,1),"rho":-150000.0,"hedge":True},
      {"name":"Jun 27","expiry":date(2027,6,1),"rho":-400000.0,"hedge":True},
      {"name":"Dec 27","expiry":date(2027,12,1),"rho":500000.0,"hedge":True},
      {"name":"Feb 28","expiry":date(2028,2,1),"rho":-1250000.0,"hedge":True},
      {"name":"Sep 28","expiry":date(2028,9,1),"rho":-250000.0,"hedge":True},
      {"name":"Dec 28","expiry":date(2028,12,1),"rho":800000.0,"hedge":True}]
    reset_widget_keys()
    st.session_state.result=None
    st.session_state.result_signature=None

if "val" not in st.session_state: st.session_state.val=date.today()
if "buckets" not in st.session_state: st.session_state.buckets=[]
if "result" not in st.session_state: st.session_state.result=None
if "result_signature" not in st.session_state: st.session_state.result_signature=None
invalidate_if_inputs_changed()

r=st.session_state.result
st.markdown('<div class="hero"><div class="brand">ATLAS · RATES RISK</div><div class="title">RHO</div><div class="live"><span class="dot"></span>MOBILE WORKSTATION</div></div>',unsafe_allow_html=True)

if r:
    side="S" if r["target_net"]<0 else "L" if r["target_net"]>0 else "—"
    total_rho=float(r.get("total_company_rho",r["company_rho"]))
    selected_rho=float(r.get("selected_company_rho",r["company_rho"]))
    open_rho=float(r.get("intentional_open_rho",0.0))
    company_status="PARTIAL" if abs(open_rho)>0.005 else "EXACT ✓"
    st.markdown(f'''<div class="kgrid">
    <div class="kpi"><div class="kl">COMPANY RHO</div><div class="kv">{money_short(total_rho)}</div></div>
    <div class="kpi"><div class="kl">RHO TO HEDGE</div><div class="kv">{money_short(selected_rho)}</div></div>
    <div class="kpi"><div class="kl">INTENTIONAL OPEN</div><div class="kv">{money_short(open_rho)}</div></div>
    <div class="kpi"><div class="kl">COMPANY HEDGE STATUS</div><div class="kv {'good' if company_status.startswith('EXACT') else ''}">{company_status}</div></div></div>''',unsafe_allow_html=True)

else:
    st.markdown('''<div class="kgrid"><div class="kpi"><div class="kl">COMPANY RHO</div><div class="kv">—</div></div><div class="kpi"><div class="kl">RHO TO HEDGE</div><div class="kv">—</div></div><div class="kpi"><div class="kl">INTENTIONAL OPEN</div><div class="kv">—</div></div><div class="kpi"><div class="kl">COMPANY HEDGE STATUS</div><div class="kv">—</div></div></div>''',unsafe_allow_html=True)

rho_tab,execute,curve,risk_tab=st.tabs(["RHO","EXECUTE","CURVE","RISK"])

with rho_tab:
    a,b=st.columns([1.45,1])
    a.date_input("Valuation",key="val")
    b.button("LOAD TEST",use_container_width=True,on_click=load_test)

    st.markdown('<div class="section">RHO INPUT</div>',unsafe_allow_html=True)
    st.caption("Excel-style input: month, company rho and whether that month is included in the hedge.")

    input_df=pd.DataFrame([
        {"Month":x["expiry"],"Rho €":float(x["rho"]),"Hedge":bool(x.get("hedge",True))}
        for x in st.session_state.buckets
    ])
    if input_df.empty:
        input_df=pd.DataFrame([{"Month":st.session_state.val,"Rho €":0.0,"Hedge":True}])

    edited=st.data_editor(
        input_df,
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        column_config={
            "Month":st.column_config.DateColumn("Month",format="MMM-YY",required=True),
            "Rho €":st.column_config.NumberColumn("Rho €",format="%.0f",step=10000.0,required=True),
            "Hedge":st.column_config.CheckboxColumn("Hedge",default=True)
        },
        key="rho_grid"
    )

    c1,c2=st.columns(2)
    if c1.button("APPLY INPUT",type="primary",use_container_width=True):
        nb=[]
        for i,row in edited.iterrows():
            if pd.isna(row["Month"]) or pd.isna(row["Rho €"]): continue
            d=pd.Timestamp(row["Month"]).date().replace(day=1)
            nb.append({"name":d.strftime("%b %y"),"expiry":d,"rho":float(row["Rho €"]),"hedge":bool(row["Hedge"])})
        st.session_state.buckets=nb
        st.session_state.result=None
        st.session_state.result_signature=None
        st.rerun()

    # Excel import: first sheet, columns Month / Rho / Hedge (Hedge optional).
    xls=c2.file_uploader("IMPORT EXCEL",type=["xlsx"],label_visibility="collapsed",key="rho_excel")
    if xls is not None:
        try:
            xd=pd.read_excel(xls)
            cm={str(c).strip().lower():c for c in xd.columns}
            mc=next((cm[k] for k in ("month","maand","expiry") if k in cm),None)
            rc=next((cm[k] for k in ("rho €","rho","company rho") if k in cm),None)
            hc=next((cm[k] for k in ("hedge","hedge?") if k in cm),None)
            if mc is None or rc is None:
                st.error("Excel needs columns Month and Rho. Hedge is optional.")
            else:
                nb=[]
                for _,row in xd.iterrows():
                    d=pd.to_datetime(row[mc],errors="coerce")
                    rho=pd.to_numeric(row[rc],errors="coerce")
                    if pd.isna(d) or pd.isna(rho): continue
                    dd=d.date().replace(day=1)
                    hv=True if hc is None or pd.isna(row[hc]) else bool(row[hc])
                    nb.append({"name":dd.strftime("%b %y"),"expiry":dd,"rho":float(rho),"hedge":hv})
                if nb and st.button("LOAD EXCEL INTO RHO",use_container_width=True):
                    st.session_state.buckets=nb
                    st.session_state.result=None
                    st.session_state.result_signature=None
                    st.rerun()
        except Exception as e:
            st.error(f"Excel import error: {e}")

    if st.session_state.buckets:
        st.markdown('<div class="section">HEDGE MATRIX · DV01 €/BP</div>',unsafe_allow_html=True)
        mx=hedge_matrix(st.session_state.val,st.session_state.buckets)
        display_mx=mx.copy()
        dvcols=[c for c in display_mx.columns if c not in ("Rho month","Rho €","Hedge")]
        for c in dvcols:
            display_mx[c]=pd.to_numeric(display_mx[c],errors="coerce").round(2)
        st.dataframe(display_mx,use_container_width=True,hide_index=True)
        selected_mx=mx[mx["Hedge"]==True] if not mx.empty else mx
        totals={c:float(pd.to_numeric(selected_mx[c],errors="coerce").fillna(0).sum()) for c in dvcols}
        target_row={c:int(round(totals[c]/25.0)) for c in dvcols}
        check=pd.DataFrame([
            {"Check":"TOTAL SELECTED DV01",**{c:round(totals[c],2) for c in dvcols}},
            {"Check":"TARGET FUTURES",**target_row}
        ])
        st.caption("CONTROL TOTALS")
        st.dataframe(check,use_container_width=True,hide_index=True)
        st.caption("Iedere rij is één rho-maand. De kwartaalcellen tonen exact hoe die rho als DV01 over de Euribor-futurescontracten wordt verdeeld.")

    with st.expander("⚙  EXECUTION ASSUMPTIONS"):
        c1,c2=st.columns(2)
        c1.number_input("Outright bp",0.0,20.0,.5,.125,key="ass_out")
        c2.number_input("Pack bp",0.0,20.0,.5,.125,key="ass_pack")
        for y,v in {2:.5,3:.625,4:.625,5:.625,6:.625}.items():
            st.number_input(f"{y}Y Bundle bp",0.0,20.0,v,.125,key=f"bundle_{y}")
    ass=execution_assumptions_from_state()

    if st.button("CALCULATE HEDGE",type="primary",use_container_width=True):
        if not st.session_state.buckets:
            st.warning("Add at least one rho month.")
        else:
            selected=[x for x in st.session_state.buckets if x.get("hedge",True)]
            total_rho=sum(float(x["rho"]) for x in st.session_state.buckets)
            selected_rho=sum(float(x["rho"]) for x in selected)
            open_rho=total_rho-selected_rho
            if selected:
                bs=[Bucket(x["name"],x["expiry"],float(x["rho"])) for x in selected]
                rr=full_result(st.session_state.val,bs,assumptions=ass)
            else:
                all_bs=[Bucket(x["name"],x["expiry"],float(x["rho"])) for x in st.session_state.buckets]
                base=full_result(st.session_state.val,all_bs,assumptions=ass)
                rr=dict(base)
                rr.update({"company_rho":0.0,"company_dv01":0.0,"target_net":0,"parallel_residual_dv01":0.0,
                           "company":[0.0]*len(base["contracts"]),"target":[0]*len(base["contracts"]),
                           "current":[0]*len(base["contracts"]),"trade":[0]*len(base["contracts"]),
                           "exact_orders":[],"best_orders":[],"exact_cost":0.0,"best_cost":0.0,"saving":0.0})
            rr["selected_company_rho"]=selected_rho
            rr["selected_company_dv01"]=selected_rho/100.0
            rr["total_company_rho"]=total_rho
            rr["total_company_dv01"]=total_rho/100.0
            rr["intentional_open_rho"]=open_rho
            rr["intentional_open_dv01"]=open_rho/100.0
            rr["selected_parallel_residual_dv01"]=float(rr["parallel_residual_dv01"])
            rr["total_post_hedge_dv01"]=open_rho/100.0+float(rr["parallel_residual_dv01"])
            st.session_state.result=rr
            st.session_state.result_signature=input_signature()
            st.rerun()

    # Excel output contains input, allocation matrix and current hedge result.
    if st.session_state.buckets:
        out=io.BytesIO()
        mx=hedge_matrix(st.session_state.val,st.session_state.buckets)
        inp=pd.DataFrame([{"Month":x["expiry"],"Rho €":x["rho"],"Hedge":x.get("hedge",True)} for x in st.session_state.buckets])
        with pd.ExcelWriter(out,engine="openpyxl") as w:
            inp.to_excel(w,index=False,sheet_name="Rho Input")
            mx.to_excel(w,index=False,sheet_name="Hedge Matrix")
            if r:
                pd.DataFrame({"Contract":r["contracts"],"Target futures":r["target"],"Trade":r["trade"]}).to_excel(w,index=False,sheet_name="Hedge Target")
                pd.DataFrame(r["best_orders"],columns=["Strategy","Start index","Length","Quantity"]).to_excel(w,index=False,sheet_name="Best Execution")
        st.download_button("EXPORT EXCEL",data=out.getvalue(),file_name="ATLAS_RHO.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           use_container_width=True)


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
            rec=pd.DataFrame({"Contract":r["contracts"],"Target":r["target"],"Trade":r["trade"]})
            rows="".join(
                f'<div class="reconrow"><span>{html.escape(str(x.Contract))}</span><span>{int(x.Target):+d}</span><span>{int(x.Trade):+d}</span></div>'
                for x in rec.itertuples(index=False)
            )
            st.markdown(
                '<div class="recon"><div class="reconhead"><span>CONTRACT</span><span>TARGET</span><span>TRADE</span></div>'+rows+'</div>',
                unsafe_allow_html=True
            )

with curve:
    st.subheader('HOE VERANDERDE DE EURIBOR-CURVE?')
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
                        p2=plot[[xcol,'Implied rate %']].copy()
                        p2[xcol]=p2[xcol].map(contract_label)
                        st.altair_chart(dark_line_chart(p2,xcol,['Implied rate %']),use_container_width=True)
                        with st.expander('CURVE TABLE'): st.dataframe(plot[[xcol,pcol,'Implied rate %']],use_container_width=True,hide_index=True)
                        st.caption('Implied rate = 100 - futures price. Descriptive curve shape only; not an ECB forecast.')
            except Exception as e: st.error(f'Could not read curve CSV: {e}')
    else:
        st.caption('Kies twee meetmomenten. Iedere lijn toont de volledige beschikbare Euribor-futurescurve op die datum.')
        try:
            hu=pd.read_csv('atlas_euribor_history.csv')
        except Exception:
            hu=None
        if hu is None:
            st.error('Built-in history database not found.')
        else:
            try:
                hist=normalize_history(hu); dates=available_dates(hist)
                if not dates:
                    st.error('No valid history dates found.')
                else:
                    min_d=dates[0].date(); max_d=dates[-1].date()
                    default_1=dates[max(0,len(dates)-126)].date()
                    default_2=dates[-1].date()
                    c1,c2=st.columns(2)
                    d1=c1.date_input('MEETMOMENT 1',value=default_1,min_value=min_d,max_value=max_d,key='curve_date_1')
                    d2=c2.date_input('MEETMOMENT 2',value=default_2,min_value=min_d,max_value=max_d,key='curve_date_2')

                    def nearest_curve(chosen):
                        target=pd.Timestamp(chosen).normalize()
                        actual=min(dates,key=lambda d:abs((d-target).days))
                        z=hist[hist['date']==actual][['contract','implied_rate']].copy()
                        z['contract']=z['contract'].map(contract_label)
                        return actual,z

                    a1,z1=nearest_curve(d1); a2,z2=nearest_curve(d2)
                    one=z1.rename(columns={'implied_rate':a1.date().strftime('%d-%m-%Y')}).set_index('contract')
                    two=z2.rename(columns={'implied_rate':a2.date().strftime('%d-%m-%Y')}).set_index('contract')
                    wide=one.join(two,how='outer')
                    labs=list(wide.columns)

                    long=wide.reset_index().melt(id_vars='contract',value_vars=labs,var_name='Meetmoment',value_name='Rate').dropna()
                    order=list(wide.index)
                    ymin=float(long['Rate'].min()); ymax=float(long['Rate'].max()); pad=max((ymax-ymin)*.22,.08)
                    chart=alt.Chart(long).mark_line(point=True,strokeWidth=3).encode(
                        x=alt.X('contract:N',sort=order,title='Euribor futures contract',axis=alt.Axis(labelAngle=-45)),
                        y=alt.Y('Rate:Q',title='Implied rate %',scale=alt.Scale(domain=[ymin-pad,ymax+pad],zero=False)),
                        color=alt.Color('Meetmoment:N',legend=alt.Legend(orient='bottom',title=None)),
                        tooltip=['contract:N','Meetmoment:N',alt.Tooltip('Rate:Q',format='.3f')]
                    ).properties(height=360,background='#080a0d').configure_view(strokeOpacity=0,fill='#080a0d').configure_axis(
                        gridColor='#252b33',domainColor='#4a515c',tickColor='#4a515c',labelColor='#aeb5bf',titleColor='#aeb5bf'
                    ).configure_legend(labelColor='#aeb5bf')
                    st.altair_chart(chart,use_container_width=True,theme=None)
                    st.caption(f'Meetmoment 1: {a1.date().strftime("%d-%m-%Y")} · Meetmoment 2: {a2.date().strftime("%d-%m-%Y")} · volledige beschikbare curves.')
                    with st.expander('CURVE TABLE'):
                        st.dataframe(wide.reset_index(),use_container_width=True,hide_index=True)
                    st.caption('Iedere lijn is de volledige beschikbare Euribor-futurescurve op dat meetmoment. Het verschil tussen de twee lijnen is de verandering van de curve.')
            except Exception as e:
                st.error(f'History error: {type(e).__name__}: {e}')

with risk_tab:
    st.markdown('<div class="section">RISK AFTER HEDGE</div>',unsafe_allow_html=True)
    if not r:
        st.info("Calculate the company risk first.")
    else:
        open_rho=float(r.get("intentional_open_rho",0.0))
        open_dv01=float(r.get("intentional_open_dv01",0.0))
        rounding=float(r.get("selected_parallel_residual_dv01",r["parallel_residual_dv01"]))
        total_post=float(r.get("total_post_hedge_dv01",open_dv01+rounding))
        partial=abs(open_rho)>0.005
        status="PARTIAL" if partial else "EXACT ✓"
        sub="selected scope exact · intentional open remains" if partial else "full company rho selected"
        st.markdown(
            '<div class="kgrid">'
            f'<div class="kpi"><div class="kl">HEDGE STATUS</div><div class="kv {"good" if not partial else ""}">{status}</div><div class="rowsub">{sub}</div></div>'
            f'<div class="kpi"><div class="kl">INTENTIONAL OPEN</div><div class="kv">{money_short(open_rho)}</div><div class="rowsub">{money_short(open_dv01)}/bp</div></div>'
            f'<div class="kpi"><div class="kl">SELECTED ROUNDING</div><div class="kv">{money_short(rounding)}/bp</div></div>'
            f'<div class="kpi"><div class="kl">POST-HEDGE DV01</div><div class="kv">{money_short(total_post)}/bp</div></div>'
            '</div>',unsafe_allow_html=True
        )
        st.markdown('<div class="section">PARALLEL MOVE · FIRST ORDER</div>',unsafe_allow_html=True)
        scenarios=[-50,-25,-10,10,25,50]
        pnl=[0.0 if abs(-total_post*x)<0.005 else round(-total_post*x,2) for x in scenarios]
        sdf=pd.DataFrame({"Move (bp)":scenarios,"P&L €":pnl})
        st.dataframe(
            sdf,use_container_width=True,hide_index=True,
            column_config={"Move (bp)":st.column_config.NumberColumn("Move (bp)",format="%d"),
                           "P&L €":st.column_config.NumberColumn("P&L €",format="€ %.2f")}
        )
        st.caption("P&L = − post-hedge DV01 × rate move. Dit is alleen first-order parallel rho/DV01.")
        st.markdown(
            '<div class="order"><div class="orderhead"><span class="side">KNOWN LIMIT</span><span class="qty">RHO-ONLY DATA</span></div>'
            '<div class="route">Curve / basis / timing risk is niet betrouwbaar in euro’s te kwantificeren.</div>'
            '<div class="rowsub">Rho per expiry is voldoende voor de afgesproken hedge, maar niet voor nonlinear repricing, rho drift of convexity.</div></div>',
            unsafe_allow_html=True
        )

st.markdown("---")
st.caption("ATLAS RHO · Mobile V6.3 · corrected bucket-to-quarter validation")
