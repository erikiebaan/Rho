# ATLAS RHO Mobile V1
Mobile-first Streamlit front end using the shared audited ATLAS RHO calculation engine.

Run locally:
`pip install -r requirements.txt`
`streamlit run atlas_rho_mobile.py`

Regression check:
`python test_atlas_rho_parity.py`

Known audited case: -€850,000 rho = -€8,500/bp, target 340 SHORT, approximately €0/bp parallel residual.

For production/company use, deploy behind authenticated HTTPS.
