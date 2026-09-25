from datetime import date
from atlas_rho_engine import Bucket,full_result
b=[Bucket("Nov26",date(2026,11,1),-100000),Bucket("Jan27",date(2027,1,1),-150000),Bucket("Jun27",date(2027,6,1),-400000),Bucket("Sep28",date(2028,9,1),-250000),Bucket("Dec27",date(2027,12,1),500000),Bucket("Feb28",date(2028,2,1),-1250000),Bucket("Dec28",date(2028,12,1),800000)]
r=full_result(date(2026,9,25),b)
assert r["company_rho"]==-850000 and r["company_dv01"]==-8500 and r["target_net"]==-340 and abs(r["parallel_residual_dv01"])<1e-9
print("ATLAS desktop/mobile parity regression: PASS")
