from datetime import date
import io
import pandas as pd
import streamlit as st

from atlas_rho_engine import Bucket, full_result

st.set_page_config(page_title="ATLAS RHO", page_icon="◼", layout="centered", initial_sidebar_state="collapsed")

ACCENT="#d8ff32"
st.markdown(f"""
<style>
:root{{color-scheme:dark}}
.stApp{{background:#080a0d;color:#f7f8fa}}
.block-container{{max-width:720px;padding:1rem 1rem 5rem}}
header[data-testid="stHeader"]{{background:transparent}}
#MainMenu,footer{{visibility:hidden}}
[data-testid='stToolbar']{{visibility:hidden;height:0}}
[data-testid='stDecoration']{{display:none}}
.brand{{font-size:.72rem;letter-spacing:.24em;color:#8b929e;font-weight:700;margin-top:.3rem}}
.title{{font-size:2.15rem;font-weight:650;letter-spacing:-.035em;margin:.2rem 0 .25rem}}
.sub{{font-size:.9rem;color:#8b929e;margin-bottom:1.2rem}}
.section{{font-size:.72rem;letter-spacing:.13em;color:#7e8794;font-weight:700;margin:1.2rem 0 .55rem}}
.stButton>button{{border-radius:14px;min-height:50px;font-weight:750;border:0;background:{ACCENT};color:#090b0e}}
[data-testid='stFileUploaderDropzone']{{background:#111419!important;border:1px solid #252a31!important;border-radius:17px!important}}
[data-testid='stFileUploaderDropzone'] button{{background:#171b21!important;color:#f5f6f7!important;border:1px solid #292f38!important}}
[data-testid="stDataFrame"]{{border-radius:14px;overflow:hidden}}
.result{{background:#111419;border:1px solid #252a31;border-radius:18px;padding:14px 16px;margin:.6rem 0}}
.resultgrid{{display:grid;grid-template-columns:1.2fr .8fr .7fr;gap:8px;align-items:center}}
.rh{{font-size:.65rem;color:#7e8794;letter-spacing:.1em;font-weight:700}}
.rv{{font-size:1.08rem;font-weight:650;margin-top:5px}}
.side{{color:{ACCENT}}}
</style>
""",unsafe_allow_html=True)

def parse_excel(upload):
    df=pd.read_excel(upload)
    cols={str(c).strip().lower():c for c in df.columns}
    mc=next((cols[k] for k in ("month","maand","expiry") if k in cols),None)
    rc=next((cols[k] for k in ("rho €","rho","company rho") if k in cols),None)
    if mc is None or rc is None:
        raise ValueError("Excel needs columns Month and Rho.")
    rows=[]
    for _,x in df.iterrows():
        d=pd.to_datetime(x[mc],errors="coerce")
        rho=pd.to_numeric(x[rc],errors="coerce")
        if pd.isna(d) or pd.isna(rho):
            continue
        dd=d.date().replace(day=1)
        rows.append({"expiry":dd,"rho":float(rho)})
    return rows

if "simple_rows" not in st.session_state:
    st.session_state.simple_rows=[{"expiry":date.today().replace(day=1),"rho":0.0}]
if "simple_result" not in st.session_state:
    st.session_state.simple_result=None

st.markdown('<div class="brand">ATLAS · RATES RISK</div><div class="title">RHO</div><div class="sub">Company rho in. Euribor hedge out.</div>',unsafe_allow_html=True)

valuation=st.date_input("Valuation date",value=date.today())

st.markdown('<div class="section">RHO INPUT</div>',unsafe_allow_html=True)
input_df=pd.DataFrame([{"Month":x["expiry"],"Rho €":x["rho"]} for x in st.session_state.simple_rows])
edited=st.data_editor(
    input_df,
    use_container_width=True,
    hide_index=True,
    num_rows="dynamic",
    column_config={
        "Month":st.column_config.DateColumn("Month",format="MMM-YY",required=True),
        "Rho €":st.column_config.NumberColumn("Rho €",format="%.0f",step=10000.0,required=True),
    },
    key="simple_rho_grid"
)

st.markdown('<div class="section">OR UPLOAD EXCEL</div>',unsafe_allow_html=True)
xls=st.file_uploader("Upload Excel",type=["xlsx"],label_visibility="collapsed")
if xls is not None:
    try:
        imported=parse_excel(xls)
        if imported and st.button("USE EXCEL INPUT",use_container_width=True):
            st.session_state.simple_rows=imported
            st.session_state.simple_result=None
            if "simple_rho_grid" in st.session_state:
                del st.session_state["simple_rho_grid"]
            st.rerun()
    except Exception as e:
        st.error(str(e))

if st.button("CALCULATE HEDGE",type="primary",use_container_width=True):
    rows=[]
    for _,x in edited.iterrows():
        if pd.isna(x["Month"]) or pd.isna(x["Rho €"]):
            continue
        d=pd.Timestamp(x["Month"]).date().replace(day=1)
        rows.append({"expiry":d,"rho":float(x["Rho €"])})
    if not rows:
        st.warning("Enter at least one rho month.")
    else:
        st.session_state.simple_rows=rows
        buckets=[Bucket(x["expiry"].strftime("%b %y"),x["expiry"],x["rho"]) for x in rows]
        rr=full_result(valuation,buckets)
        st.session_state.simple_result={"valuation":valuation,"rows":rows,"result":rr}
        st.rerun()

saved=st.session_state.simple_result
if saved:
    rr=saved["result"]
    st.markdown('<div class="section">HEDGE</div>',unsafe_allow_html=True)
    trades=[]
    for contract,q in zip(rr["contracts"],rr["target"]):
        if not q:
            continue
        side="BUY" if q>0 else "SELL"
        trades.append({"Contract":contract,"Side":side,"Quantity":abs(int(q))})
        st.markdown(
            f'<div class="result"><div class="resultgrid">'
            f'<div><div class="rh">CONTRACT</div><div class="rv">{contract}</div></div>'
            f'<div><div class="rh">SIDE</div><div class="rv side">{side}</div></div>'
            f'<div><div class="rh">QUANTITY</div><div class="rv">{abs(int(q))}</div></div>'
            f'</div></div>',unsafe_allow_html=True
        )
    if not trades:
        st.success("No hedge required.")

    out=io.BytesIO()
    with pd.ExcelWriter(out,engine="openpyxl") as w:
        pd.DataFrame([{"Month":x["expiry"],"Rho €":x["rho"]} for x in saved["rows"]]).to_excel(w,index=False,sheet_name="Rho Input")
        pd.DataFrame(trades,columns=["Contract","Side","Quantity"]).to_excel(w,index=False,sheet_name="Hedge Result")
    st.download_button(
        "DOWNLOAD EXCEL",
        data=out.getvalue(),
        file_name="ATLAS_RHO_HEDGE.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True
    )

st.markdown("---")
st.caption("ATLAS RHO · Simple V1")
