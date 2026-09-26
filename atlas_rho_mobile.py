from datetime import date
import io, math
import pandas as pd
import streamlit as st

st.set_page_config(page_title="ATLAS RHO",page_icon="◼",layout="centered",initial_sidebar_state="collapsed")

ACCENT="#d8ff32"
st.markdown(f"""
<style>
:root{{color-scheme:dark}} .stApp{{background:#080a0d;color:#f7f8fa}}
.block-container{{max-width:720px;padding:1rem 1rem 5rem}}
header[data-testid="stHeader"]{{background:transparent}}
#MainMenu,footer,[data-testid='stToolbar'],[data-testid='stDecoration']{{display:none!important}}
.brand{{font-size:.72rem;letter-spacing:.24em;color:#8b929e;font-weight:700}}
.title{{font-size:2.15rem;font-weight:650;margin:.2rem 0 1.2rem}}
.section{{font-size:.72rem;letter-spacing:.13em;color:#7e8794;font-weight:700;margin:1.2rem 0 .55rem}}
.stButton>button{{border-radius:14px;min-height:50px;font-weight:750;border:0;background:{ACCENT};color:#090b0e}}
[data-testid="stDataFrame"]{{border-radius:14px;overflow:hidden}}
</style>""",unsafe_allow_html=True)

# ------------------------------------------------------------------
# ATLAS RHO — EXCEL SPECIFICATION (locked)
#
# Source of truth: euribor_rho_hedge_model.xlsx
#
# Excel formulas:
#   DV01 = Rho / 100
#   Hedge quarter = expiry rounded UP to Mar/Jun/Sep/Dec
#   First hedge contract = quarter rounded UP from valuation + 1 month
#   N quarters = months(first contract -> hedge quarter)/3 + 1
#   Bucket DV01 is divided equally across those N quarterly contracts
#   Contract DV01 = sum of all bucket contributions that pass that contract
#   Target futures = Excel ROUND(contract DV01 / 25, 0)
#
# IMPORTANT:
#   A bucket whose hedge quarter is BEFORE the first hedge contract is
#   outside the valid model horizon. The Excel was designed for current/
#   future rho buckets; we reject such input instead of silently rolling it.
# ------------------------------------------------------------------

def add_months(d,n):
    y=d.year+(d.month-1+n)//12
    m=(d.month-1+n)%12+1
    return date(y,m,1)

def quarter_ceil(d):
    m=((d.month+2)//3)*3
    return date(d.year,m,1)

def excel_round(x):
    return int(math.floor(x+0.5)) if x>=0 else int(math.ceil(x-0.5))

def first_hedge_contract(valuation):
    return quarter_ceil(add_months(valuation,1))

def excel_hedge(valuation,rows):
    front=first_hedge_contract(valuation)
    prepared=[]
    for x in rows:
        expiry=x["Month"]
        rho=float(x["Rho €"])
        hq=quarter_ceil(expiry)
        if hq < front:
            raise ValueError(
                f"{expiry.strftime('%b-%y')} ligt vóór het eerste hedgecontract "
                f"{front.strftime('%b-%y')} voor waarderingsdatum {valuation.strftime('%d-%m-%Y')}."
            )
        n=((hq.year-front.year)*12+(hq.month-front.month))//3+1
        prepared.append((rho/100.0,hq,n))

    last=max([x[1] for x in prepared],default=front)
    contracts=[]; d=front
    while d<=last:
        contracts.append(d); d=add_months(d,3)

    contract_dv01={d:0.0 for d in contracts}
    for bucket_dv01,hq,n in prepared:
        contribution=bucket_dv01/n
        for d in contracts:
            if d<=hq:
                contract_dv01[d]+=contribution

    trades=[]
    for d in contracts:
        q=excel_round(contract_dv01[d]/25.0)
        if q:
            trades.append({
                "Contract":d.strftime("%b-%y"),
                "Side":"BUY" if q>0 else "SELL",
                "Quantity":abs(q),
            })
    return trades

if "input_rows" not in st.session_state:
    st.session_state.input_rows=[{"Month":date.today().replace(day=1),"Rho €":0.0}]
if "result" not in st.session_state:
    st.session_state.result=None
if "error" not in st.session_state:
    st.session_state.error=None

st.markdown('<div class="brand">ATLAS · RATES RISK</div><div class="title">RHO</div>',unsafe_allow_html=True)
valuation=st.date_input("Valuation date",value=date.today())

st.markdown('<div class="section">INPUT</div>',unsafe_allow_html=True)
edited=st.data_editor(
    pd.DataFrame(st.session_state.input_rows),
    use_container_width=True,hide_index=True,num_rows="dynamic",
    column_config={
        "Month":st.column_config.DateColumn("Month",format="MMM-YY",required=True),
        "Rho €":st.column_config.NumberColumn("Rho €",format="%.0f",step=10000.0,required=True)
    }
)

if st.button("CALCULATE HEDGE",type="primary",use_container_width=True):
    rows=[]
    for _,x in edited.iterrows():
        if pd.isna(x["Month"]) or pd.isna(x["Rho €"]):
            continue
        rows.append({
            "Month":pd.Timestamp(x["Month"]).date().replace(day=1),
            "Rho €":float(x["Rho €"])
        })
    st.session_state.input_rows=rows
    try:
        st.session_state.result=excel_hedge(valuation,rows) if rows else []
        st.session_state.error=None
    except ValueError as e:
        st.session_state.result=None
        st.session_state.error=str(e)
    st.rerun()

if st.session_state.error:
    st.error(st.session_state.error)

if st.session_state.result is not None:
    st.markdown('<div class="section">HEDGE</div>',unsafe_allow_html=True)
    if st.session_state.result:
        st.dataframe(pd.DataFrame(st.session_state.result),use_container_width=True,hide_index=True)
    else:
        st.write("No hedge required.")

    out=io.BytesIO()
    with pd.ExcelWriter(out,engine="openpyxl") as w:
        pd.DataFrame(st.session_state.input_rows).to_excel(w,index=False,sheet_name="Input")
        pd.DataFrame(st.session_state.result,columns=["Contract","Side","Quantity"]).to_excel(w,index=False,sheet_name="Hedge")
    st.download_button(
        "DOWNLOAD EXCEL",out.getvalue(),"ATLAS_RHO.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True
    )

st.caption("ATLAS RHO · Excel specification V1")
