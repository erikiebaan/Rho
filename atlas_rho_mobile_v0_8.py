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
    # Exact LOCKED V17 rule: first contract = quarter-ceil of valuation + 1 month.
    return hedge_quarter(add_months(v,1))

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
    front=qs[0]
    for d,rho in exposures:
        # Same roll-edge rule as the canonical V17 engine: if a rho bucket
        # falls in/past the rolled quarter, carry it into the first available future.
        h=max(front,hedge_quarter(d))
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

def engine_integrity_check():
    """Golden regression check: protects the locked Base and Analysed mappings."""
    exposures=[(parse_month(m),v) for m,v in DEFAULT_EXPOSURE]
    contracts,target,analysed=analysed_target_and_lots(exposures)
    base,_=align_base(exposures,contracts)
    names=[fmt_month(x[0]) for x in contracts]

    expected_base={
        "Dec-26":144,"Mar-27":104,"Jun-27":74,"Sep-27":20,"Dec-27":20,
        "Mar-28":60,"Jun-28":-23,"Sep-28":-23,"Dec-28":-36
    }
    expected_analysed={
        "Oct-26":24,"Nov-26":16,"Dec-26":49,"Mar-27":37,"Jun-27":134,
        "Sep-27":-31,"Dec-27":67,"Mar-28":264,"Jun-28":21,"Sep-28":12,"Dec-28":-253
    }
    got_base={n:int(v) for n,v in zip(names,base) if int(v)!=0}
    got_analysed={n:int(v) for n,v in zip(names,analysed) if int(v)!=0}
    if got_base != expected_base or got_analysed != expected_analysed:
        raise RuntimeError("ATLAS RHO engine integrity check failed")

EXEC_DEFAULTS = {
    "outright_spread_bp":0.5,
    "pack_spread_bp":0.5,
    "bundle_2y_spread_bp":0.5,
    "bundle_3y_spread_bp":0.625,
    "bundle_4y_spread_bp":0.625,
    "bundle_5y_spread_bp":0.625,
    "bundle_6y_spread_bp":0.625,
    "outright_fee":0.0,
    "strategy_fee":0.0,
}

def _quarter_gap_months(a,b):
    return (b.year-a.year)*12 + (b.month-a.month)

def _strategy_name(L):
    return "Pack" if L==4 else f"{L//4}Y Bundle"

def execution_strategy_universe(contracts):
    """Exact V17 pack/bundle universe, adapted to the mobile contract list."""
    allowed=(4,8,12,16,20,24)
    universe=[]
    for L in allowed:
        for st in range(0,len(contracts)-L+1):
            window=contracts[st:st+L]
            if any(x[2]!="QUARTER" for x in window):
                continue
            months=[x[0] for x in window]
            if any(_quarter_gap_months(months[i],months[i+1])!=3 for i in range(L-1)):
                continue
            universe.append((_strategy_name(L),st,L))
    return universe

def execution_order_cost(order,assumptions):
    name,st,L,q=order
    qty=abs(int(q))
    if name=="Outright":
        spread=float(assumptions["outright_spread_bp"])
        fee=float(assumptions.get("outright_fee",0.0))
    elif name=="Pack":
        spread=float(assumptions["pack_spread_bp"])
        fee=float(assumptions.get("strategy_fee",0.0))
    else:
        years=L//4
        spread=float(assumptions[f"bundle_{years}y_spread_bp"])
        fee=float(assumptions.get("strategy_fee",0.0))
    return qty*(spread/2.0)*KR01_PER_FUTURE + qty*fee

def optimize_exact_execution(contracts,required,assumptions):
    """V17 execution principle: minimize cost subject to exact leg-for-leg reconstruction."""
    from scipy.optimize import milp, LinearConstraint, Bounds
    from scipy.sparse import lil_matrix

    req=np.asarray([int(x) for x in required],dtype=float)
    n=len(req)
    instruments=[("Outright",i,1) for i in range(n)]
    instruments += execution_strategy_universe(contracts)
    m=len(instruments)

    cost=np.zeros(2*m)
    A=lil_matrix((n,2*m),dtype=float)
    for j,(name,st,L) in enumerate(instruments):
        unit=execution_order_cost((name,st,L,1),assumptions)
        cost[j]=unit
        cost[m+j]=unit
        for i in range(st,st+L):
            A[i,j]=1.0
            A[i,m+j]=-1.0

    result=milp(
        c=cost,
        integrality=np.ones(2*m),
        bounds=Bounds(np.zeros(2*m),np.full(2*m,np.inf)),
        constraints=LinearConstraint(A.tocsr(),req,req),
        options={"presolve":True},
    )
    if not result.success or result.x is None:
        raise RuntimeError(f"Execution optimizer failed: {result.message}")

    orders=[]
    for j,(name,st,L) in enumerate(instruments):
        q=int(round(result.x[j]-result.x[m+j]))
        if q:
            orders.append((name,st,L,q))
    orders.sort(key=lambda x:(x[1],x[2],x[0]))

    recon=[0]*n
    for name,st,L,q in orders:
        for i in range(st,st+L):
            recon[i]+=q
    target=[int(x) for x in req]
    if recon!=target:
        raise RuntimeError("Execution reconciliation failed: quarterly target changed.")

    outright=[("Outright",i,1,int(q)) for i,q in enumerate(req) if int(q)]
    exact_cost=sum(execution_order_cost(o,assumptions) for o in outright)
    best_cost=sum(execution_order_cost(o,assumptions) for o in orders)
    return {
        "orders":orders,
        "reconstructed":recon,
        "reference_cost":exact_cost,
        "best_cost":best_cost,
        "saving":exact_cost-best_cost,
        "difference_dv01":sum((recon[i]-target[i])*KR01_PER_FUTURE for i in range(n)),
        "reference_orders":len(outright),
    }

def execution_integrity_check():
    """Regression guard: optimizer may change execution only, never the selected hedge."""
    exposures=[(parse_month(m),v) for m,v in DEFAULT_EXPOSURE]
    contracts,_,_=analysed_target_and_lots(exposures)
    base,_=align_base(exposures,contracts)
    ex=optimize_exact_execution(contracts,base,EXEC_DEFAULTS)
    if ex["reconstructed"] != [int(x) for x in base]:
        raise RuntimeError("execution hedge reconstruction differs from selected hedge")
    if abs(ex["difference_dv01"]) > 1e-12:
        raise RuntimeError("execution curve difference is not zero")
    if ex["best_cost"] > ex["reference_cost"] + 1e-9:
        raise RuntimeError("execution optimizer is more expensive than all-outright reference")

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
    """Validated 20% sensitivity: Locked V17 vs Analysed against the unchanged true curve.

    Each observed monthly Rho bucket is independently perturbed with 20% Gaussian
    measurement noise. Both hedges see the same perturbed observation; both are then
    judged against the original unperturbed curve target. Score = % runs in which
    Analysed has lower worst non-parallel curve mismatch than Locked V17.
    """
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
    # Blank mobile editor cells mean zero exposure; never let None/NaN reach the hedge engine.
    clean_values=[0.0 if pd.isna(v) else float(v) for v in values]
    exposures=[(parse_month(m),v) for m,v in zip(months,clean_values)]
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

st.set_page_config(page_title="ATLAS RHO Mobile",page_icon="◼",layout="centered",initial_sidebar_state="collapsed")

try:
    engine_integrity_check()
    execution_integrity_check()
except Exception as exc:
    st.error(f"ENGINE LOCK FAILED · {exc}")
    st.stop()
st.markdown("""
<style>
:root{--bg:#06101a;--panel:#0b1825;--line:#173b5c;--text:#f2f6fb;--muted:#8fa4b8;--red:#ff7078;--green:#5de4b3;--amber:#ffd267}
.stApp{background:var(--bg);color:var(--text)}
.block-container{max-width:760px;padding:.65rem .75rem 4rem}
header[data-testid="stHeader"]{background:transparent}
#MainMenu,footer,[data-testid='stToolbar']{display:none!important}
h1{font-size:1.65rem!important;margin:0!important;line-height:1.1}
.sub{color:var(--muted);font-size:.66rem;letter-spacing:.13em;margin:.18rem 0 .65rem}
.lock{background:#0b211d;border:1px solid #174638;color:#62deb5;border-radius:10px;padding:.52rem .7rem;font-size:.66rem;font-weight:800;margin-bottom:.45rem}
.section{font-size:.68rem;letter-spacing:.1em;color:#a9c4df;font-weight:900;margin:.8rem 0 .38rem}
.db{border-radius:12px;padding:.65rem .8rem;text-align:center;font-size:1.25rem;font-weight:900;margin:.15rem 0 .45rem}
.base{background:#332608;border:1px solid #a77811;color:var(--amber)}
.ana{background:#0b3328;border:1px solid #1b8c69;color:var(--green)}
.gates{display:grid;grid-template-columns:1fr 1fr;gap:.45rem;margin:.25rem 0}
.gate{border-radius:10px;padding:.55rem .62rem;min-height:74px}
.good{background:#0a2a24;border:1px solid #167b60}.bad{background:#291219;border:1px solid #7f2635}
.gl{font-size:.58rem;color:#b9c9d8;font-weight:900;letter-spacing:.04em}.gv{font-size:1.05rem;font-weight:900;margin:.08rem 0}.gs{font-size:.67rem;color:#c5d2df}
.reason{background:#09131d;border:1px solid #173653;border-radius:10px;padding:.6rem .7rem;color:#c5d2df;font-size:.72rem;margin:.4rem 0 .55rem}
.order-head{display:grid;grid-template-columns:1fr 64px 48px;gap:.35rem;padding:0 .65rem .2rem;color:#6f879d;font-size:.57rem;font-weight:900;letter-spacing:.08em}
.order{display:grid;grid-template-columns:1fr 64px 48px;gap:.35rem;align-items:center;background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:.48rem .65rem;margin:.25rem 0}
.contract{font-size:.82rem;font-weight:800}.sell{color:var(--red);font-weight:900;font-size:.72rem}.buy{color:var(--green);font-weight:900;font-size:.72rem}.qty{font-size:.98rem;font-weight:900;text-align:right}
.kpis{display:grid;grid-template-columns:1fr 1fr;gap:.4rem;margin:.45rem 0}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:.58rem .65rem}
.kl{font-size:.57rem;color:#8fa4b8;font-weight:900;letter-spacing:.05em}.kv{font-size:1.05rem;color:#f4f7fb;font-weight:900;margin-top:.08rem}
.maprow{background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:.5rem .62rem;margin:.28rem 0}
.maptop{display:flex;justify-content:space-between;gap:.5rem;font-size:.76rem;font-weight:850}.mapbot{color:#91a8bd;font-size:.66rem;margin-top:.18rem}
.rule{background:#09131d;border:1px solid #173653;border-radius:10px;padding:.65rem .72rem;color:#c9d6e2;font-size:.72rem;margin:.35rem 0}\n.curveh{display:grid;grid-template-columns:1fr 74px 74px;gap:.35rem;padding:.1rem .55rem .25rem;color:#70879c;font-size:.57rem;font-weight:900;letter-spacing:.08em}.curver{display:grid;grid-template-columns:1fr 74px 74px;gap:.35rem;align-items:center;background:#0b1825;border:1px solid #173b5c;border-radius:9px;padding:.45rem .55rem;margin:.22rem 0;font-size:.72rem}.cv{text-align:right;font-weight:850}.cb{color:#8fc8ff}.ca{color:#f2f6fb}
.execsum{display:grid;grid-template-columns:1fr 1fr 1fr;gap:.35rem;margin:.35rem 0}.execk{background:#0b1825;border:1px solid #173b5c;border-radius:9px;padding:.48rem .5rem;text-align:center}.exl{font-size:.52rem;color:#8fa4b8;font-weight:900}.exv{font-size:.88rem;font-weight:900;margin-top:.08rem}.execrow{background:#0b1825;border:1px solid #173b5c;border-radius:9px;padding:.5rem .6rem;margin:.25rem 0}.exectop{display:flex;justify-content:space-between;gap:.5rem;font-size:.76rem;font-weight:900}.execbot{color:#8fa4b8;font-size:.62rem;margin-top:.15rem}
div[data-testid="stMetric"]{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:8px}
div[data-testid="stMetricLabel"]{color:#9db1c4!important}
div[data-testid="stMetricValue"]{color:#f2f6fb!important}
.stButton>button,.stDownloadButton>button{border-radius:10px;min-height:40px;font-weight:800}
[data-testid="stDataFrame"],[data-testid="stDataEditor"]{border:1px solid #173653;border-radius:10px;overflow:hidden}
div[data-baseweb="tab-list"]{gap:.1rem}
button[data-baseweb="tab"]{padding-left:.45rem!important;padding-right:.45rem!important;font-size:.78rem!important}
[data-testid="stExpander"]{border:1px solid #173653!important;border-radius:10px!important;background:#09131d}
hr{border-color:#14283a}
</style>""",unsafe_allow_html=True)

st.title("ATLAS RHO")
st.markdown('<div class="sub">INTEREST RATE RISK · MOBILE EXECUTION DESK</div>',unsafe_allow_html=True)
st.markdown('<div class="lock">● LOCKED BASE V17 · ANALYSED V2.2 · 10.000 RUNS</div>',unsafe_allow_html=True)

if "rho08" not in st.session_state:
    st.session_state.rho08=pd.DataFrame(DEFAULT_EXPOSURE,columns=["Maand","Rho / +100 bp"])

tabs=st.tabs(["UITVOEREN","RHO","CURVE","RISK"])

# RHO is rendered second, but calculated first so UITVOEREN always uses the current engine result.
with tabs[1]:
    st.markdown('<div class="section">RHO EXPOSURE PER MAAND</div>',unsafe_allow_html=True)
    edited=st.data_editor(
        st.session_state.rho08,use_container_width=True,hide_index=True,num_rows="fixed",
        column_config={
            "Maand":st.column_config.TextColumn(disabled=True),
            "Rho / +100 bp":st.column_config.NumberColumn(format="€ %.0f",step=10000)
        },key="editor08"
    )
    # On iPhone, deleting a number produces None. Interpret an empty Rho cell as €0.
    edited["Rho / +100 bp"]=pd.to_numeric(edited["Rho / +100 bp"],errors="coerce").fillna(0.0)
    st.session_state.rho08=edited
    if st.button("HERBEREKEN",type="primary",use_container_width=True):
        st.cache_data.clear()
    result=evaluate(tuple(edited["Maand"]),tuple(edited["Rho / +100 bp"]))
    net=float(edited["Rho / +100 bp"].sum())
    gross=float(edited["Rho / +100 bp"].abs().sum())
    st.markdown(
        f'<div class="kpis">'
        f'<div class="kpi"><div class="kl">NET RHO</div><div class="kv">{euro(net,True)}</div></div>'
        f'<div class="kpi"><div class="kl">GROSS RHO</div><div class="kv">{euro(gross)}</div></div>'
        f'<div class="kpi"><div class="kl">BASE CURVE-RISICO</div><div class="kv">{euro(result["base_worst"])}</div></div>'
        f'<div class="kpi"><div class="kl">ANALYSED CURVE-RISICO</div><div class="kv">{euro(result["analysed_worst"])}</div></div>'
        f'</div>',unsafe_allow_html=True
    )
    with st.expander("BASE MAPPING · toon uitleg"):
        current_rows=list(zip(edited["Maand"].tolist(),edited["Rho / +100 bp"].astype(float).tolist()))
        for (m,rho),(h,eligible,alloc) in zip(current_rows,result["base_detail"]):
            direction="SELL" if alloc>0 else "BUY"
            st.markdown(
                f'<div class="maprow"><div class="maptop"><span>{m} · {euro(rho,True)}</span>'
                f'<span>→ {fmt_month(h)}</span></div>'
                f'<div class="mapbot">{fmt_month(eligible[0])} → {fmt_month(eligible[-1])} · '
                f'~{abs(alloc/KR01_PER_FUTURE):.0f} {direction} per kwartaalcontract</div></div>',
                unsafe_allow_html=True
            )

with tabs[0]:
    r=result
    is_a=r["decision"]=="ANALYSED"
    mat=r["benefit"]>=MATERIALITY_EUR
    rob=r["robustness"]>=ROBUSTNESS_MIN
    st.markdown(f'<div class="db {"ana" if is_a else "base"}">BESLUIT · {r["decision"]}</div>',unsafe_allow_html=True)
    st.markdown(
        f'<div class="gates">'
        f'<div class="gate {"good" if mat else "bad"}"><div class="gl">MATERIEEL</div>'
        f'<div class="gv">{euro(r["benefit"])}</div><div class="gs">grens €10k {"✓" if mat else "✕"}</div></div>'
        f'<div class="gate {"good" if rob else "bad"}"><div class="gl">20% SENSITIVITEIT</div>'
        f'<div class="gv">{r["robustness"]:.1f}%</div><div class="gs">grens 80% {"✓" if rob else "✕"}</div></div>'
        f'</div>',unsafe_allow_html=True
    )
    reason=("Analysed is materieel én robuust genoeg." if is_a else
            "Base blijft leidend: Analysed is niet materieel genoeg." if not mat else
            "Base blijft leidend: Analysed haalt de 80%-sensitiviteitsgrens niet.")
    st.markdown(f'<div class="reason">{reason}</div>',unsafe_allow_html=True)

    selected=r["analysed"] if is_a else r["base"]

    # Execution assumptions live in session state so BEST EXACT can stay visually first.
    exec_defaults={
        "ex_out":0.5,"ex_pack":0.5,"ex_b2":0.5,"ex_bl":0.625,
        "ex_ofee":0.0,"ex_sfee":0.0,
    }
    for k,v in exec_defaults.items():
        if k not in st.session_state:
            st.session_state[k]=v
    assumptions={
        "outright_spread_bp":float(st.session_state.ex_out),
        "pack_spread_bp":float(st.session_state.ex_pack),
        "bundle_2y_spread_bp":float(st.session_state.ex_b2),
        "bundle_3y_spread_bp":float(st.session_state.ex_bl),
        "bundle_4y_spread_bp":float(st.session_state.ex_bl),
        "bundle_5y_spread_bp":float(st.session_state.ex_bl),
        "bundle_6y_spread_bp":float(st.session_state.ex_bl),
        "outright_fee":float(st.session_state.ex_ofee),
        "strategy_fee":float(st.session_state.ex_sfee),
    }
    exec_contracts=[]
    for (label,kind) in r["contracts"]:
        d=parse_month(label)
        exec_contracts.append((d,third_wednesday(d.year,d.month),kind))

    st.markdown('<div class="section">BEST EXACT EXECUTION</div>',unsafe_allow_html=True)
    st.caption("Dit is wat je uitvoert. Zelfde kwartaalhedge, efficiënter opgebouwd met outrights, Packs en Bundles.")

    try:
        ex=optimize_exact_execution(exec_contracts,selected,assumptions)
        st.markdown(
            f'<div class="execsum">'
            f'<div class="execk"><div class="exl">ORDERS</div><div class="exv">{len(ex["orders"])}</div></div>'
            f'<div class="execk"><div class="exl">EST. SAVING</div><div class="exv">{euro(max(0,ex["saving"]))}</div></div>'
            f'<div class="execk"><div class="exl">CURVE Δ</div><div class="exv">{euro(ex["difference_dv01"])}/bp</div></div>'
            f'</div>',unsafe_allow_html=True
        )
        exec_lines=[]
        for name,stx,L,q in ex["orders"]:
            action="SELL" if q>0 else "BUY"
            qty=abs(int(q))
            first=r["contracts"][stx][0]
            last=r["contracts"][stx+L-1][0]
            instrument=(f"3M Euribor {first}" if name=="Outright" else name)
            period=(first if L==1 else f"{first} → {last}")
            exec_lines.append(f"{instrument} | {action} | {qty} | {period}")
            st.markdown(
                f'<div class="execrow"><div class="exectop"><span>{instrument}</span>'
                f'<span class="{"sell" if action=="SELL" else "buy"}">{action} {qty}</span></div>'
                f'<div class="execbot">{period} · exacte reconstructie</div></div>',
                unsafe_allow_html=True
            )

        st.markdown(
            f'<div class="reason"><b>CONTROLE ✓ · CURVE Δ €0/bp</b><br>'
            f'De uitvoering reconstrueert de gekozen {r["decision"]}-hedge contract voor contract. '
            f'All-outright: {ex["reference_orders"]} orders, est. {euro(ex["reference_cost"])} · '
            f'Best exact: {len(ex["orders"])} orders, est. {euro(ex["best_cost"])}.</div>',
            unsafe_allow_html=True
        )
        st.download_button(
            "BEST EXECUTION ORDERLIJST",data="\n".join(exec_lines),
            file_name=f"ATLAS_RHO_{r['decision']}_BEST_EXECUTION.txt",
            mime="text/plain",use_container_width=True,key="best_exec_download"
        )

        with st.expander("Onderliggende exacte kwartaalhedge"):
            st.caption("Audit: dit is de hedge die bovenstaande Packs/Bundles exact moeten reconstrueren.")
            st.markdown('<div class="order-head"><span>CONTRACT</span><span>ACTIE</span><span style="text-align:right">LOTS</span></div>',unsafe_allow_html=True)
            hedge_lines=[]
            for (contract,kind),lots in zip(r["contracts"],selected):
                if not lots:
                    continue
                action="SELL" if lots>0 else "BUY"
                qty=abs(int(lots))
                hedge_lines.append(f"3M Euribor {contract} | {action} | {qty}")
                st.markdown(
                    f'<div class="order"><div class="contract">3M Euribor {contract}</div>'
                    f'<div class="{"sell" if action=="SELL" else "buy"}">{action}</div>'
                    f'<div class="qty">{qty}</div></div>',unsafe_allow_html=True
                )
            st.download_button(
                "ONDERLIGGENDE HEDGE",data="\n".join(hedge_lines),
                file_name=f"ATLAS_RHO_{r['decision']}_UNDERLYING_HEDGE.txt",
                mime="text/plain",use_container_width=True,key="underlying_download"
            )

        with st.expander("Execution aannames"):
            ec1,ec2=st.columns(2)
            ec1.number_input("Outright spread (bp)",min_value=0.0,step=0.125,key="ex_out")
            ec2.number_input("Pack spread (bp)",min_value=0.0,step=0.125,key="ex_pack")
            ec3,ec4=st.columns(2)
            ec3.number_input("2Y Bundle spread (bp)",min_value=0.0,step=0.125,key="ex_b2")
            ec4.number_input("3Y+ Bundle spread (bp)",min_value=0.0,step=0.125,key="ex_bl")
            fee1,fee2=st.columns(2)
            fee1.number_input("Outright fee / lot €",min_value=0.0,step=0.10,key="ex_ofee")
            fee2.number_input("Strategy fee / unit €",min_value=0.0,step=0.10,key="ex_sfee")
            st.caption("Kosten zijn schattingen op basis van half de ingevoerde full bid/ask spread plus fees; geen gegarandeerde fill-kosten.")

    except Exception as exc:
        st.error(f"Execution optimizer: {exc}")

with tabs[2]:
    st.markdown('<div class="section">CURVE · BASE VS ANALYSED</div>',unsafe_allow_html=True)
    st.markdown('<div class="curveh"><span>CONTRACT</span><span style="text-align:right">BASE</span><span style="text-align:right">ANALYSED</span></div>',unsafe_allow_html=True)
    for (contract,kind),b,a in zip(result["contracts"],result["base"],result["analysed"]):
        st.markdown(
            f'<div class="curver"><b>{contract}</b><span class="cv cb">{lot_text(b)}</span><span class="cv ca">{lot_text(a)}</span></div>',
            unsafe_allow_html=True
        )
    with st.expander("Wat verandert er precies?"):
        comp=pd.DataFrame({
            "Contract":[x[0] for x in result["contracts"]],
            "Verschil":[diff_text(b,a) for b,a in zip(result["base"],result["analysed"])]
        })
        st.dataframe(comp,use_container_width=True,hide_index=True)

with tabs[3]:
    st.markdown('<div class="section">WAAROM KIEST ATLAS DIT?</div>',unsafe_allow_html=True)
    st.markdown(
        '<div class="rule"><b>BASE V17</b><br>'
        'Verdeelt de DV01 van iedere Rho-maand gelijk over alle kwartaal-Euribors vanaf het frontcontract tot het hedgekwartaal.</div>',
        unsafe_allow_html=True
    )
    st.markdown(
        '<div class="rule"><b>ANALYSED V2.2</b><br>'
        'Plaatst dezelfde hedge-KR01 preciezer op de curve via de maand→IMM mapping, inclusief de twee front serials.</div>',
        unsafe_allow_html=True
    )
    st.markdown(
        f'<div class="rule"><b>BESLISREGEL</b><br>'
        f'Analysed alleen bij ≥ €10.000 lager curve-risico én ≥ 80% beter in de 20%-sensitiviteitstest met 10.000 runs. '
        f'Daarin krijgen BASE V17 en ANALYSED exact dezelfde verstoorde maand-Rho en worden beide beoordeeld tegen de oorspronkelijke curve. '
        f'Nu: {euro(result["benefit"])} en {result["robustness"]:.1f}%.</div>',
        unsafe_allow_html=True
    )
    st.markdown('<div class="rule"><b>BELANGRIJK</b><br>20% is een stresstest, geen gemeten foutpercentage. UITVOEREN is geen derde hedge: het toont exact de gekozen BASE- of ANALYSED-hedge.</div>',unsafe_allow_html=True)
    with st.expander("Stress-scenario's"):
        scen=pd.DataFrame({
            "Scenario":[x[0] for x in result["base_scen"]],
            "Base €":[x[1] for x in result["base_scen"]],
            "Analysed €":[x[1] for x in result["analysed_scen"]]
        })
        st.dataframe(scen,use_container_width=True,hide_index=True)

st.caption("ATLAS RHO Mobile · Locked V17 Base · V2.2 Analysed")
