from datetime import date
import io, math
import pandas as pd
import streamlit as st
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment

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
.compare{{background:#11151b;border:1px solid #252b34;border-radius:16px;overflow:hidden;margin:.35rem 0 .7rem}}
.compare-head,.compare-row{{display:grid;grid-template-columns:1.1fr 1fr 1fr;align-items:center}}
.compare-head{{background:#0d1015;color:#8b929e;font-size:.66rem;letter-spacing:.10em;font-weight:800}}
.compare-head>div,.compare-row>div{{padding:10px 12px;border-right:1px solid #252b34}}
.compare-head>div:last-child,.compare-row>div:last-child{{border-right:0}}
.compare-row{{border-top:1px solid #252b34;font-size:.86rem}}
.compare-row .base{{font-weight:800;color:#f7f8fa}}
.compare-row .analysed{{color:#8b929e}}
.why{{font-size:.76rem;color:#8b929e;margin:.35rem .1rem .75rem;line-height:1.35}}
.locked{{color:#d8ff32;font-weight:800}}
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
    st.session_state.input_rows=[{"Month":date(2027,12,1),"Rho €":-600000.0}]
if "result" not in st.session_state:
    st.session_state.result=None
if "error" not in st.session_state:
    st.session_state.error=None
if "curve_rates" not in st.session_state:
    st.session_state.curve_rates={
        "Dec-26":2.0,
        "Mar-27":2.5,
        "Jun-27":3.5,
        "Sep-27":4.5,
        "Dec-27":3.5,
    }

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
    if not st.session_state.result:
        st.write("No hedge required.")

    st.markdown('<div class="section">BASE HEDGE → ANALYSED HEDGE</div>',unsafe_allow_html=True)
    compare_rows=[]
    for t in st.session_state.result:
        base=("BUY " if t["Side"]=="BUY" else "SELL ")+str(t["Quantity"])
        compare_rows.append(
            f'<div class="compare-row"><div>{t["Contract"]}</div>'
            f'<div class="base">{base}</div><div class="analysed">—</div></div>'
        )
    st.markdown(
        '<div class="compare">'
        '<div class="compare-head"><div>CONTRACT</div><div>BASE</div><div>ANALYSED</div></div>'
        + ''.join(compare_rows) +
        '</div>'
        '<div class="why"><span class="locked">BASE V1 LOCKED</span> · Analysed Hedge volgt pas uit gevalideerde curve- en risicoanalyse.</div>',
        unsafe_allow_html=True
    )

    tab_curve,tab_risk,tab_alt=st.tabs(["CURVE","RISK","ALTERNATIVES"])
    with tab_curve:
        st.caption("Actuele curve · alleen analyse — verandert Base V1 nooit.")
        curve_contracts=[t["Contract"] for t in st.session_state.result]
        curve_df=pd.DataFrame({
            "Contract":curve_contracts,
            "Rate %":[st.session_state.curve_rates.get(c,None) for c in curve_contracts]
        })
        curve_edit=st.data_editor(
            curve_df,use_container_width=True,hide_index=True,
            disabled=["Contract"],
            column_config={
                "Contract":st.column_config.TextColumn("Contract"),
                "Rate %":st.column_config.NumberColumn("Rate %",format="%.3f",step=0.01)
            },
            key="curve_editor_"+"_".join(curve_contracts)
        )
        valid_curve=curve_edit.dropna(subset=["Rate %"]).copy()
        for _,r in valid_curve.iterrows():
            st.session_state.curve_rates[str(r["Contract"])]=float(r["Rate %"])
        if len(valid_curve)>=2:
            # Explicit Vega-Lite spec prevents Streamlit's mobile line-chart autoscale/state issue.
            curve_plot=valid_curve[["Contract","Rate %"]].copy()
            curve_plot["Rate %"]=pd.to_numeric(curve_plot["Rate %"],errors="coerce")
            st.vega_lite_chart(
                curve_plot,
                {
                    "mark":{"type":"line","point":True,"strokeWidth":3},
                    "encoding":{
                        "x":{
                            "field":"Contract","type":"ordinal","sort":None,
                            "axis":{"title":None,"labelAngle":-45}
                        },
                        "y":{
                            "field":"Rate %","type":"quantitative",
                            "scale":{"zero":False},
                            "axis":{"title":"Rate %","format":".2f"}
                        },
                        "tooltip":[
                            {"field":"Contract","type":"ordinal"},
                            {"field":"Rate %","type":"quantitative","format":".3f"}
                        ]
                    },
                    "height":220
                },
                use_container_width=True
            )
            first=float(valid_curve.iloc[0]["Rate %"])
            last=float(valid_curve.iloc[-1]["Rate %"])
            slope=(last-first)*100.0
            shape="UPWARD" if slope>1 else ("DOWNWARD" if slope<-1 else "FLAT")
            c1,c2=st.columns(2)
            c1.metric("Front → back",f"{slope:+.1f} bp")
            c2.metric("Shape",shape)
            st.caption("Dit beschrijft alleen de huidige curvevorm. De analysed hedge wordt hier nog niet uit afgeleid.")
        else:
            st.caption("Vul minimaal twee actuele Euribor-rentes in om de curve te zien.")
    with tab_risk:
        st.caption("Base V1 onder curve-scenario's · analyse only.")
        if len(valid_curve)<2:
            st.info("Vul eerst minimaal twee rentes in bij CURVE.")
        else:
            n=len(valid_curve)
            base_by_contract={t["Contract"]:(t["Quantity"] if t["Side"]=="BUY" else -t["Quantity"]) for t in st.session_state.result}
            contracts=valid_curve["Contract"].astype(str).tolist()
            base_qty=[base_by_contract.get(c,0) for c in contracts]

            # Signed contract DV01: +25 for BUY, -25 for SELL.
            # Scenario P&L is first-order futures DV01 only; it deliberately does not
            # pretend we know the option's key-rate rho from total rho + expiry.
            dv01=[q*25.0 for q in base_qty]
            x=[i/(n-1) for i in range(n)]
            scenarios={
                "PARALLEL +25":[25.0]*n,
                "FRONT +25":[25.0*(1-v) for v in x],
                "BACK +25":[25.0*v for v in x],
                "STEEPENER":[-12.5+25.0*v for v in x],
                "FLATTENER":[12.5-25.0*v for v in x],
            }
            risk_rows=[]
            for name,moves in scenarios.items():
                hedge_move=sum(d*m for d,m in zip(dv01,moves))
                risk_rows.append({"Scenario":name,"Base hedge Δ €":round(hedge_move,0)})
            risk_df=pd.DataFrame(risk_rows)
            st.dataframe(
                risk_df,use_container_width=True,hide_index=True,
                column_config={
                    "Scenario":st.column_config.TextColumn("Scenario"),
                    "Base hedge Δ €":st.column_config.NumberColumn("Hedge Δ €",format="€ %.0f")
                }
            )
            st.caption(
                "Dit is de beweging van de futures-hedge zelf bij gestandaardiseerde curve-shocks. "
                "Residual optie-P&L tonen we bewust nog niet: daarvoor hebben we key-rate rho nodig "
                "of een expliciet te valideren allocatiemodel."
            )
    with tab_alt:
        st.caption("Daarna vergelijken we alternatieven altijd tegen Base V1.")

    # Professional master-style Excel export; app hedge logic stays untouched.
    wb=Workbook()
    ws=wb.active; ws.title="Model"
    res=wb.create_sheet("Resultaat")
    ins=wb.create_sheet("Instructies")
    risk=wb.create_sheet("Risico's")
    log=wb.create_sheet("Trade Log")
    proc=wb.create_sheet("Proces & Uitvoering")
    navy="17365D"; yellow="FFF2CC"; white="FFFFFF"

    def make_title(sh,text,subtitle=""):
        sh.merge_cells("A1:M1"); sh["A1"]=text
        sh["A1"].font=Font(size=18,bold=True,color=white)
        sh["A1"].fill=PatternFill("solid",fgColor=navy)
        if subtitle:
            sh.merge_cells("A2:M2"); sh["A2"]=subtitle
            sh["A2"].font=Font(italic=True,color="666666")

    def make_header(cell):
        cell.font=Font(bold=True,color=white)
        cell.fill=PatternFill("solid",fgColor=navy)
        cell.alignment=Alignment(wrap_text=True,vertical="center")

    make_title(ws,"Euribor Rho-Hedge Model","Rho-buckets → automatische strip-hedge in 3-Maands Euribor futures")
    ws["A4"]="Waarderingsdatum:"; ws["B4"]=valuation
    ws["B4"].number_format="dd-mm-yyyy"; ws["B4"].fill=PatternFill("solid",fgColor=yellow)
    ws["A6"]="RHO-BUCKETS (uit ATLAS RHO)"; ws["A6"].font=Font(bold=True,color=navy)
    heads=["Bucket naam","Expiry datum","Rho (EUR/100bp)","DV01 (EUR/bp)","Hedge-kwartaal","# kwartalen"]
    for j,h in enumerate(heads,1): ws.cell(7,j).value=h; make_header(ws.cell(7,j))

    front=first_hedge_contract(valuation)
    prepared=[]
    for i,x in enumerate(st.session_state.input_rows,start=8):
        hq=quarter_ceil(x["Month"])
        n=((hq.year-front.year)*12+(hq.month-front.month))//3+1
        prepared.append((float(x["Rho €"])/100.0,hq,n))
        vals=[x["Month"].strftime("%b %Y"),x["Month"],float(x["Rho €"]),float(x["Rho €"])/100.0,hq,n]
        for j,v in enumerate(vals,1): ws.cell(i,j).value=v
        ws.cell(i,2).number_format=ws.cell(i,5).number_format="mmm-yy"
        ws.cell(i,3).number_format='#,##0'; ws.cell(i,4).number_format='#,##0.00'
        for j in (1,2,3): ws.cell(i,j).fill=PatternFill("solid",fgColor=yellow)

    ladder=max(10,10+len(st.session_state.input_rows))
    ws.cell(ladder,1).value="HEDGE-LADDER"; ws.cell(ladder,1).font=Font(bold=True,color=navy)
    lh=["Contractmaand","Netto DV01 toegewezen","Doelpositie","Aantal","Richting"]
    for j,h in enumerate(lh,1): ws.cell(ladder+1,j).value=h; make_header(ws.cell(ladder+1,j))
    last=max([p[1] for p in prepared],default=front); contracts=[]; d=front
    while d<=last: contracts.append(d); d=add_months(d,3)
    for r,d in enumerate(contracts,ladder+2):
        dv=sum(v/n for v,hq,n in prepared if d<=hq); q=excel_round(dv/25.0)
        vals=[d,dv,q,abs(q),"KOOP (long)" if q>0 else ("VERKOOP (short)" if q<0 else "-")]
        for j,v in enumerate(vals,1): ws.cell(r,j).value=v
        ws.cell(r,1).number_format="mmm-yy"; ws.cell(r,2).number_format='#,##0.00'

    make_title(res,"Netto Euribor Hedge — uit te voeren per contractmaand","Gebaseerd op ATLAS RHO")
    for j,h in enumerate(["Contractmaand","Doelpositie","Richting","Aantal"],1): res.cell(4,j).value=h; make_header(res.cell(4,j))
    for r,t in enumerate(st.session_state.result,start=5):
        signed=t["Quantity"] if t["Side"]=="BUY" else -t["Quantity"]
        for j,v in enumerate([t["Contract"],signed,"KOOP" if t["Side"]=="BUY" else "VERKOOP",t["Quantity"]],1): res.cell(r,j).value=v

    make_title(ins,"Euribor Rho-Hedge Model — Instructies")
    txt=["DV01 = Rho / 100.","Expiry wordt naar het eerstvolgende kwartaal afgerond.","Bucket-DV01 wordt gelijk verdeeld over de kwartaalstrip tot expiry.","1 Euribor future = EUR 25 per basispunt.","Doelpositie = toegewezen DV01 / 25, afgerond op hele contracten.","Positief = KOOP; negatief = VERKOOP."]
    for r,t in enumerate(txt,3): ins.cell(r,1).value="•  "+t

    make_title(risk,"Risico's van deze Euribor-strip hedge")
    for r,t in enumerate(["Eerste-orde DV01-benadering; geen convexiteit.","Basisrisico Euribor versus discountcurve.","Vlakke kwartaalverdeling is een vereenvoudiging.","Rho verandert met tijd en portefeuille.","Afronding naar hele futures laat een residu over."],3): risk.cell(r,1).value="•  "+t

    make_title(log,"Trade Log — historie van herhedges")
    for j,h in enumerate(["Datum herijking","Contractmaand","Doelpositie","Richting","Notities"],1): log.cell(4,j).value=h; make_header(log.cell(4,j))
    make_title(proc,"Proces & Uitvoering")
    proc["A3"]="Gebruik Resultaat als uitvoerblad. Leg uitgevoerde herhedges desgewenst vast in Trade Log."

    for sh in wb.worksheets:
        for col,w in {"A":30,"B":20,"C":22,"D":20,"E":22,"F":16,"G":18,"H":18,"I":18,"J":18,"K":18,"L":18,"M":18}.items(): sh.column_dimensions[col].width=w
        for row in sh.iter_rows():
            for cell in row: cell.alignment=Alignment(vertical="top",wrap_text=True)

    out=io.BytesIO(); wb.save(out)
    st.download_button("DOWNLOAD EXCEL",out.getvalue(),"ATLAS_RHO.xlsx","application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",use_container_width=True)

st.caption("ATLAS RHO · Excel specification V1")
