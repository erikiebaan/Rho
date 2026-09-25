from datetime import date
import streamlit as st
from atlas_rho_engine import Bucket,full_result,DEFAULT_EXECUTION
st.set_page_config(page_title="ATLAS RHO Mobile",page_icon="◼",layout="centered")
st.markdown("""<style>.block-container{max-width:760px;padding-top:1.2rem}div[data-testid="stMetric"]{border:1px solid #262b36;border-radius:14px;padding:12px;background:#11151d}h1{font-size:1.65rem!important}</style>""",unsafe_allow_html=True)
st.title("ATLAS RHO"); st.caption("Mobile Risk Manager • same exact hedge engine")
with st.expander("Company input",expanded=True):
    val=st.date_input("Valuation date",value=date.today()); n=st.number_input("Number of rho buckets",1,20,5,1); buckets=[]
    for i in range(int(n)):
        c=st.columns([1.25,1,1])
        name=c[0].text_input("Name",f"Bucket {i+1}",key=f"n{i}",label_visibility="collapsed")
        exp=c[1].date_input("Expiry",key=f"d{i}",label_visibility="collapsed")
        rho=c[2].number_input("Rho",value=0.0,step=10000.0,key=f"r{i}",label_visibility="collapsed")
        buckets.append(Bucket(name,exp,float(rho)))
ass=dict(DEFAULT_EXECUTION)
with st.expander("Execution assumptions"):
    ass["outright_spread_bp"]=st.number_input("Outright full spread (bp)",0.0,20.0,0.5,0.125)
    ass["pack_spread_bp"]=st.number_input("Pack full spread (bp)",0.0,20.0,0.5,0.125)
    for y,v in [(2,.5),(3,.625),(4,.625),(5,.625),(6,.625)]: ass[f"bundle_{y}y_spread_bp"]=st.number_input(f"{y}Y Bundle full spread (bp)",0.0,20.0,v,.125)
if st.button("Calculate exact hedge",type="primary",use_container_width=True):
    try: st.session_state["result"]=full_result(val,buckets,assumptions=ass)
    except Exception as e: st.error(str(e))
r=st.session_state.get("result")
if r:
    a,b=st.columns(2); a.metric("Company Rho",f"€{r['company_rho']:,.0f}"); b.metric("Company DV01",f"€{r['company_dv01']:,.0f}/bp")
    a,b=st.columns(2); side="SHORT" if r["target_net"]<0 else "LONG" if r["target_net"]>0 else "FLAT"
    a.metric("Target Hedge",f"{abs(r['target_net']):,} {side}"); b.metric("Parallel Residual",f"€{r['parallel_residual_dv01']:,.1f}/bp")
    st.subheader("Best execution"); st.metric("Estimated saving",f"€{r['saving']:,.0f}",f"Best €{r['best_cost']:,.0f} vs outright €{r['exact_cost']:,.0f}")
    rows=[]
    for name,stx,L,q in r["best_orders"]: rows.append({"Trade":"BUY" if q>0 else "SELL","Qty":abs(q),"Instrument":name,"Period":" / ".join(r["contracts"][stx:stx+L])})
    st.dataframe(rows,use_container_width=True,hide_index=True)
    with st.expander("Quarterly audit"):
        st.dataframe([{"Contract":c,"DV01":round(d,1),"Target":t,"Trade":tr} for c,d,t,tr in zip(r["contracts"],r["company"],r["target"],r["trade"])],use_container_width=True,hide_index=True)
