from datetime import date
import math
import numpy as np
import pandas as pd
import streamlit as st

# ATLAS RHO MOBILE 0.8
# LOCKED V17 BASE + V2.2 ANALYSED + 20% / 10,000 sensitivity

VALUATION = date(2026, 9, 28)
MATERIALITY_EUR = 10_000.0
ROBUSTNESS_MIN = 80.0
ROBUST_SIMS = 10_000
ROBUST_NOISE = 0.20
ROBUST_SEED = 42
KR01_PER_FUTURE = 25.0

DEFAULT_EXPOSURE = [
    ("Nov-26", -100_000.0), ("Jan-27", -150_000.0),
    ("Jun-27", -400_000.0), ("Dec-27", 500_000.0),
    ("Feb-28", -1_250_000.0), ("Sep-28", -250_000.0),
    ("Dec-28", 800_000.0),
]

def parse_month(s):
    return pd.to_datetime(s, format="%b-%y").date().replace(day=1)

def fmt_month(d):
    return d.strftime("%b-%y")

def euro(x, signed=False):
    sign = ("+" if x > 0 else "-" if x < 0 else "") if signed else ""
    return f"{sign}€{abs(float(x)):,.0f}".replace(",", ".")

def third_wednesday(y, m):
    first = date(y, m, 1)
    return date(y, m, 1 + ((2-first.weekday()) % 7) + 14)

def add_months(d, n):
    i = d.year*12+d.month-1+int(n)
    y,m = divmod(i,12)
    return date(y,m+1,1)

def hedge_quarter(d):
    return date(d.year, ((d.month-1)//3+1)*3, 1)

def first_quarter(v):
    q=hedge_quarter(v)
    if q.month==v.month and third_wednesday(q.year,q.month)<=v:
        q=add_months(q,3)
    return q

def quarter_strip(v,last):
    out=[]; d=first_quarter(v)
    while d<=last:
        out.append(d); d=add_months(d,3)
    return out

def excel_round(x):
    return int(math.floor(x+.5)) if x>=0 else int(math.ceil(x-.5))

def locked_v17_base(exposures, valuation=VALUATION):
    horizons=[hedge_quarter(d) for d,_ in exposures]
    qs=quarter_strip(valuation,max(horizons))
    kr01={q:0.0 for q in qs}; detail=[]
    for d,rho in exposures:
        h=hedge_quarter(d)
        eligible=[q for q in qs if q<=h]
        hedge=-float(rho)/100.0
        alloc=hedge/len(eligible)
        for q in eligible: kr01[q]+=alloc
        detail.append((h,eligible,alloc))
    lots={q:excel_round(v/KR01_PER_FUTURE) for q,v in kr01.items()}
    return qs,kr01,lots,detail

def analysed_contracts(valuation,bucket_dates):
    last=max(bucket_dates); out=[]; vm=date(valuation.year,valuation.month,1)
    for k in (1,2):
        d=add_months(vm,k); out.append((d,third_wednesday(d.year,d.month),"SERIAL"))
    y,m=valuation.year,valuation.month
    for _ in range(80):
        if m in (3,6,9,12):
            imm=third_wednesday(y,m)
            if imm>valuation:
                d=date(y,m,1); out.append((d,imm,"QUARTER"))
                if imm>=last: break
        m+=1
        if m==13: m=1; y+=1
    return sorted({imm:(d,imm,k) for d,imm,k in out}.values(),key=lambda z:z[1])

def analysed_target_and_lots(exposures, valuation=VALUATION):
    contracts=analysed_contracts(valuation,[d for d,_ in exposures])
    imms=[c[1] for c in contracts]
    target=np.zeros(len(contracts),dtype=float)
    for d,rho in exposures:
        d=date(d.year,d.month,1); v=-float(rho)/100.0
        if d<=imms[0]: target[0]+=v
        elif d>=imms[-1]: target[-1]+=v
        else:
            for j in range(len(imms)-1):
                if imms[j]<=d<=imms[j+1]:
                    span=max((imms[j+1]-imms[j]).days,1)
                    w=(d-imms[j]).days/span
                    target[j]+=v*(1-w); target[j+1]+=v*w
                    break
    cont=target/KR01_PER_FUTURE
    lots=np.rint(cont).astype(int)
    target_net=int(round(target.sum()/KR01_PER_FUTURE))
    gap=target_net-int(lots.sum())
    if gap:
        frac=cont-np.floor(cont)
        order=np.argsort(-frac if gap>0 else frac); k=0
        while gap:
            j=int(order[k%len(order)])
            lots[j]+=1 if gap>0 else -1
            gap += -1 if gap>0 else 1; k+=1
    return contracts,target,lots

def align_base(exposures,contracts):
    _,_,base,detail=locked_v17_base(exposures)
    return np.array([base.get(c[0],0) for c in contracts],dtype=int),detail

def scenario_values(target,lots):
    residual=target-np.asarray(lots,dtype=float)*KR01_PER_FUTURE
    n=len(residual); z=np.linspace(0,1,n) if n>1 else np.zeros(1)
    shocks=[
        ("Parallel +25",np.full(n,25.0)),
        ("Parallel -25",np.full(n,-25.0)),
        ("Front +25 → Back 0",25*(1-z)),
        ("Front 0 → Back +25",25*z),
        ("Front +25 → Back -25",25*(1-2*z)),
    ]
    vals=[(name,float(residual@shock)) for name,shock in shocks]
    worst=max(abs(v) for name,v in vals if name.startswith("Front"))
    return vals,worst

def robustness(exposures,true_target,contracts):
    rng=np.random.default_rng(ROBUST_SEED)
    dates=[d for d,_ in exposures]
    vals=np.array([v for _,v in exposures],dtype=float)
    better=0
    for _ in range(ROBUST_SIMS):
        observed=vals*(1+rng.normal(0,ROBUST_NOISE,size=len(vals)))
        trial=list(zip(dates,observed))
        trial_contracts,_,a=analysed_target_and_lots(trial)
        if [c[0] for c in trial_contracts] != [c[0] for c in contracts]:
            continue
        b,_=align_base(trial,contracts)
        _,bw=scenario_values(true_target,b); _,aw=scenario_values(true_target,a)
        if aw<bw: better+=1
    return 100*better/ROBUST_SIMS

@st.cache_data(show_spinner=False)
def evaluate(months,values):
    exposures=[(parse_month(m),float(v)) for m,v in zip(months,values)]
    contracts,target,a=analysed_target_and_lots(exposures)
    b,detail=align_base(exposures,contracts)
    bs,bw=scenario_values(target,b); ans,aw=scenario_values(target,a)
    benefit=bw-aw; robust=robustness(exposures,target,contracts)
    decision="ANALYSED" if benefit>=MATERIALITY_EUR and robust>=ROBUSTNESS_MIN else "BASE"
    return dict(exposures=exposures,contracts=[(fmt_month(c[0]),c[2]) for c in contracts],
                base=b.tolist(),analysed=a.tolist(),base_detail=detail,
                base_scen=bs,analysed_scen=ans,base_worst=bw,analysed_worst=aw,
                benefit=benefit,robustness=robust,decision=decision)

def lot_text(x):
    return f"{abs(int(x))} {'SELL' if x>0 else 'BUY'}" if x else "—"

def diff_text(b,a):
    d=int(a)-int(b)
    return f"+{d} richting SELL" if d>0 else f"{abs(d)} richting BUY" if d<0 else "0"

st.set_page_config(page_title="ATLAS RHO Mobile 0.8",page_icon="◼",layout="centered",initial_sidebar_state="collapsed")
st.markdown("""
<style>
.stApp{background:#07111b;color:#eef5ff}.block-container{max-width:760px;padding:.75rem .8rem 5rem}
header[data-testid="stHeader"]{background:transparent}#MainMenu,footer,[data-testid='stToolbar']{display:none!important}
h1{font-size:1.7rem!important;margin-bottom:0!important}.sub{color:#86a0b8;font-size:.7rem;letter-spacing:.12em;margin:.1rem 0 .8rem}
.lock{background:#0b211d;border:1px solid #174638;color:#55e2b1;border-radius:12px;padding:.65rem .8rem;font-size:.72rem;font-weight:700}
.section{font-size:.72rem;letter-spacing:.1em;color:#a8c8e7;font-weight:800;margin:1rem 0 .45rem}
.db{border-radius:14px;padding:1rem;text-align:center;font-size:1.55rem;font-weight:900}
.base{background:#332608;border:1px solid #a77811;color:#ffd267}.ana{background:#0b3328;border:1px solid #1b8c69;color:#5de4b3}
.good{background:#0a2a24;border:1px solid #167b60;border-radius:12px;padding:.75rem}.bad{background:#291219;border:1px solid #7f2635;border-radius:12px;padding:.75rem}
.gl{font-size:.65rem;color:#c5d2df;font-weight:800}.gv{font-size:1.3rem;font-weight:900}
.reason{background:#09131d;border:1px solid #173653;border-radius:12px;padding:.8rem;color:#c5d2df;font-size:.78rem;margin-top:.5rem}
.order{display:flex;justify-content:space-between;align-items:center;background:#0b1825;border:1px solid #173b5c;border-radius:12px;padding:.72rem .8rem;margin:.35rem 0}
.sell{color:#ff7078;font-weight:900}.buy{color:#49e0b0;font-weight:900}.qty{font-size:1.15rem;font-weight:900}
div[data-testid="stMetric"]{background:#0b1825;border:1px solid #173b5c;border-radius:12px;padding:10px}
.stButton>button{border-radius:12px;min-height:44px;font-weight:800}[data-testid="stDataFrame"]{border:1px solid #173653;border-radius:12px;overflow:hidden}
</style>""",unsafe_allow_html=True)

st.title("ATLAS RHO")
st.markdown('<div class="sub">INTEREST RATE RISK · MOBILE EXECUTION DESK</div>',unsafe_allow_html=True)
st.markdown('<div class="lock">● LOCKED BASE V17 · ANALYSED V2.2 · 10.000 RUNS</div>',unsafe_allow_html=True)

if "rho08" not in st.session_state:
    st.session_state.rho08=pd.DataFrame(DEFAULT_EXPOSURE,columns=["Maand","Rho / +100 bp"])
tabs=st.tabs(["RHO","UITVOEREN","CURVE","RISK"])

with tabs[0]:
    st.markdown('<div class="section">RHO EXPOSURE PER MAAND</div>',unsafe_allow_html=True)
    edited=st.data_editor(st.session_state.rho08,use_container_width=True,hide_index=True,num_rows="fixed",
        column_config={"Maand":st.column_config.TextColumn(disabled=True),
                       "Rho / +100 bp":st.column_config.NumberColumn(format="€ %.0f",step=10000)},key="editor08")
    st.session_state.rho08=edited
    if st.button("HERBEREKEN",type="primary",use_container_width=True): st.cache_data.clear()
    result=evaluate(tuple(edited["Maand"]),tuple(float(x) for x in edited["Rho / +100 bp"]))
    net=float(edited["Rho / +100 bp"].sum()); gross=float(edited["Rho / +100 bp"].abs().sum())
    c1,c2=st.columns(2); c1.metric("NET RHO",euro(net,True)); c2.metric("GROSS RHO",euro(gross))
    c1,c2=st.columns(2); c1.metric("BASE CURVE-RISICO",euro(result["base_worst"])); c2.metric("ANALYSED CURVE-RISICO",euro(result["analysed_worst"]))
    st.markdown('<div class="section">BASE MAPPING</div>',unsafe_allow_html=True)
    rows=[]
    for i,((m,rho),(h,eligible,alloc)) in enumerate(zip(DEFAULT_EXPOSURE,result["base_detail"])):
        rows.append({"Maand":m,"Rho":rho,"Hedge horizon":fmt_month(h),
                     "Base spread":f"{fmt_month(eligible[0])} → {fmt_month(eligible[-1])} · ~{abs(alloc/KR01_PER_FUTURE):.0f} {'SELL' if alloc>0 else 'BUY'} elk"})
    st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)

with tabs[1]:
    r=result; is_a=r["decision"]=="ANALYSED"
    st.markdown(f'<div class="db {"ana" if is_a else "base"}">BESLUIT: {r["decision"]}</div>',unsafe_allow_html=True)
    mat=r["benefit"]>=MATERIALITY_EUR; rob=r["robustness"]>=ROBUSTNESS_MIN
    c1,c2=st.columns(2)
    c1.markdown(f'<div class="{"good" if mat else "bad"}"><div class="gl">MATERIEEL VOORDEEL</div><div class="gv">{euro(r["benefit"])}</div><div>grens €10.000 {"✓" if mat else "✕"}</div></div>',unsafe_allow_html=True)
    c2.markdown(f'<div class="{"good" if rob else "bad"}"><div class="gl">20% SENSITIVITEIT</div><div class="gv">{r["robustness"]:.1f}%</div><div>grens 80% {"✓" if rob else "✕"}</div></div>',unsafe_allow_html=True)
    reason=("Analysed is materieel én robuust genoeg. Gebruik onderstaande Analysed orderlijst." if is_a else
            "BASE: het eurovoordeel van Analysed is te klein voor extra curve-complexiteit." if not mat else
            "BASE: het eurovoordeel is materieel, maar Analysed haalt de 80%-sensitiviteitsgrens niet.")
    st.markdown(f'<div class="reason">{reason}</div>',unsafe_allow_html=True)
    st.markdown('<div class="section">UITVOEREN · EXACTE HEDGE ORDERS</div>',unsafe_allow_html=True)
    selected=r["analysed"] if is_a else r["base"]; lines=[]
    for (contract,kind),lots in zip(r["contracts"],selected):
        if not lots: continue
        action="SELL" if lots>0 else "BUY"; qty=abs(int(lots)); lines.append(f"3M Euribor {contract} | {action} | {qty}")
        st.markdown(f'<div class="order"><div><b>3M EURIBOR {contract}</b><div class="{"sell" if action=="SELL" else "buy"}">{action}</div></div><div class="qty">{qty}</div></div>',unsafe_allow_html=True)
    st.download_button("DOWNLOAD ORDERLIJST",data="\n".join(lines),file_name=f"ATLAS_RHO_{r['decision']}_ORDERS.txt",mime="text/plain",use_container_width=True)

with tabs[2]:
    st.markdown('<div class="section">BASE VS ANALYSED · LOTS</div>',unsafe_allow_html=True)
    df=pd.DataFrame({"Contract":[c[0] for c in result["contracts"]],"Base":result["base"],"Analysed":result["analysed"]}).set_index("Contract")
    st.line_chart(df,use_container_width=True,height=320)
    comp=pd.DataFrame({"Contract":[c[0] for c in result["contracts"]],
        "Base":[lot_text(x) for x in result["base"]],"Analysed":[lot_text(x) for x in result["analysed"]],
        "Verschil":[diff_text(b,a) for b,a in zip(result["base"],result["analysed"])]})
    st.dataframe(comp,use_container_width=True,hide_index=True)

with tabs[3]:
    st.markdown('<div class="section">WAAROM KIEST ATLAS DIT?</div>',unsafe_allow_html=True)
    st.write("**BASE V17** neemt iedere maand-Rho, bepaalt het hedgekwartaal en verdeelt de DV01 gelijk over alle kwartaal-Euribors vanaf het frontcontract tot en met dat hedgekwartaal.")
    st.write("**ANALYSED V2.2** gebruikt dezelfde maand-exposure, maar plaatst de hedge-KR01 preciezer op de curve via de maand→IMM mapping, inclusief de twee front serials.")
    st.write("**Beslisregel:** Analysed alleen bij minimaal €10.000 lager worst-case curve-risico én minimaal 80% robuustheid in 10.000 runs onder 20% Rho-sensitiviteit.")
    st.info("20% is een stresstest, geen gemeten foutpercentage. UITVOEREN is geen derde hedge: het is exact BASE of ANALYSED.")
    scen=pd.DataFrame({"Scenario":[x[0] for x in result["base_scen"]],
        "Base mismatch €":[x[1] for x in result["base_scen"]],
        "Analysed mismatch €":[x[1] for x in result["analysed_scen"]]})
    st.dataframe(scen,use_container_width=True,hide_index=True)

st.caption("ATLAS RHO Mobile 0.8 · Locked V17 Base · V2.2 Analysed")
