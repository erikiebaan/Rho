from datetime import date
import io
import pandas as pd
import streamlit as st
from atlas_rho_engine import Bucket, full_result

st.set_page_config(page_title="ATLAS RHO",page_icon="◼",layout="centered",initial_sidebar_state="collapsed")
ACCENT="#d8ff32"
st.markdown(f"""
<style>
:root{{color-scheme:dark}}
.stApp{{background:#080a0d;color:#f7f8fa}}
.block-container{{max-width:720px;padding:1rem 1rem 5rem}}
header[data-testid="stHeader"]{{background:transparent}}
#MainMenu,footer,[data-testid='stToolbar'],[data-testid='stDecoration']{{display:none!important}}
.brand{{font-size:.72rem;letter-spacing:.24em;color:#8b929e;font-weight:700}}
.title{{font-size:2.15rem;font-weight:650;letter-spacing:-.035em;margin:.2rem 0}}
.sub{{font-size:.9rem;color:#8b929e;margin-bottom:1.2rem}}
.section{{font-size:.72rem;letter-spacing:.13em;color:#7e8794;font-weight:700;margin:1.2rem 0 .55rem}}
.stButton>button,.stFormSubmitButton>button{{border-radius:14px;min-height:50px;font-weight:750;border:0;background:{ACCENT};color:#090b0e}}
[data-testid='stFileUploaderDropzone']{{background:#111419!important;border:1px solid #252a31!important;border-radius:17px!important}}
[data-testid="stDataFrame"]{{border-radius:14px;overflow:hidden}}
.result{{background:#111419;border:1px solid #252a31;border-radius:18px;padding:14px 16px;margin:.6rem 0}}
.resultgrid{{display:grid;grid-template-columns:1.2fr .8fr .7fr;gap:8px}}
.rh{{font-size:.65rem;color:#7e8794;letter-spacing:.1em;font-weight:700}}
.rv{{font-size:1.08rem;font-weight:650;margin-top:5px}} .side{{color:{ACCENT}}}
</style>""",unsafe_allow_html=True)

def read_excel(f):
    df=pd.read_excel(f)
    cm={str(c).strip().lower():c for c in df.columns}
    mc=next((cm[k] for k in ("month","maand","expiry") if k in cm),None)
    rc=next((cm[k] for k in ("rho €","rho","company rho") if k in cm),None)
    if mc is None or rc is None: raise ValueError("Excel needs columns Month and Rho.")
    out=[]
    for _,x in df.iterrows():
        d=pd.to_datetime(x[mc],errors="coerce"); v=pd.to_numeric(x[rc],errors="coerce")
        if pd.isna(d) or pd.isna(v): continue
        out.append({"Month":d.date().replace(day=1),"Rho €":float(v)})
    return out

if "rows" not in st.session_state:
    st.session_state.rows=[{"Month":date.today().replace(day=1),"Rho €":0.0}]
if "answer" not in st.session_state: st.session_state.answer=None

st.markdown('<div class="brand">ATLAS · RATES RISK</div><div class="title">RHO</div><div class="sub">Company rho in. Euribor hedge out.</div>',unsafe_allow_html=True)

# Excel is only an alternative way to fill the same input.
xls=st.file_uploader("UPLOAD EXCEL",type=["xlsx"])
if xls is not None:
    try:
        imported=read_excel(xls)
        if imported and st.button("USE EXCEL",use_container_width=True):
            st.session_state.rows=imported
            st.session_state.answer=None
            if "rho_form_grid" in st.session_state: del st.session_state["rho_form_grid"]
            st.rerun()
    except Exception as e: st.error(str(e))

# One atomic form: values are read only when CALCULATE HEDGE is pressed.
with st.form("rho_calculator",clear_on_submit=False):
    valuation=st.date_input("Valuation date",value=date.today())
    st.markdown('<div class="section">RHO INPUT</div>',unsafe_allow_html=True)
    df=pd.DataFrame(st.session_state.rows)
    edited=st.data_editor(
        df,use_container_width=True,hide_index=True,num_rows="dynamic",
        column_config={
            "Month":st.column_config.DateColumn("Month",format="MMM-YY",required=True),
            "Rho €":st.column_config.NumberColumn("Rho €",format="%.0f",step=10000.0,required=True)
        },key="rho_form_grid"
    )
    calculate=st.form_submit_button("CALCULATE HEDGE",type="primary",use_container_width=True)

if calculate:
    rows=[]
    for _,x in edited.iterrows():
        if pd.isna(x["Month"]) or pd.isna(x["Rho €"]): continue
        d=pd.Timestamp(x["Month"]).date().replace(day=1)
        rows.append({"Month":d,"Rho €":float(x["Rho €"])})
    st.session_state.rows=rows
    if not rows:
        st.session_state.answer=None
        st.warning("Enter at least one rho month.")
    else:
        rr=full_result(valuation,[Bucket(x["Month"].strftime("%b %y"),x["Month"],x["Rho €"]) for x in rows])
        trades=[{"Contract":c,"Side":"BUY" if q>0 else "SELL","Quantity":abs(int(q))}
                for c,q in zip(rr["contracts"],rr["target"]) if q]
        st.session_state.answer={"valuation":valuation,"rows":rows,"trades":trades}

answer=st.session_state.answer
if answer:
    st.markdown('<div class="section">HEDGE</div>',unsafe_allow_html=True)
    if answer["trades"]:
        for x in answer["trades"]:
            st.markdown(f'<div class="result"><div class="resultgrid">'
                        f'<div><div class="rh">CONTRACT</div><div class="rv">{x["Contract"]}</div></div>'
                        f'<div><div class="rh">SIDE</div><div class="rv side">{x["Side"]}</div></div>'
                        f'<div><div class="rh">QUANTITY</div><div class="rv">{x["Quantity"]}</div></div>'
                        f'</div></div>',unsafe_allow_html=True)
    else: st.success("No hedge required.")

    out=io.BytesIO()
    with pd.ExcelWriter(out,engine="openpyxl") as w:
        pd.DataFrame(answer["rows"]).to_excel(w,index=False,sheet_name="Rho Input")
        pd.DataFrame(answer["trades"],columns=["Contract","Side","Quantity"]).to_excel(w,index=False,sheet_name="Hedge Result")
    st.download_button("DOWNLOAD EXCEL",out.getvalue(),"ATLAS_RHO_HEDGE.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       use_container_width=True)

st.markdown("---")
st.caption("ATLAS RHO · Simple V1.1")
