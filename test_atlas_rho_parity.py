from datetime import date
from atlas_rho_engine import Bucket,full_result
b=[Bucket("Nov26",date(2026,11,1),-100000),Bucket("Jan27",date(2027,1,1),-150000),Bucket("Jun27",date(2027,6,1),-400000),Bucket("Sep28",date(2028,9,1),-250000),Bucket("Dec27",date(2027,12,1),500000),Bucket("Feb28",date(2028,2,1),-1250000),Bucket("Dec28",date(2028,12,1),800000)]
r=full_result(date(2026,9,25),b)
assert r["company_rho"]==-850000 and r["company_dv01"]==-8500 and r["target_net"]==-340 and abs(r["parallel_residual_dv01"])<1e-9
print("ATLAS desktop/mobile parity regression: PASS")


# Edge cases around valuation 25-Sep-2026: no selected rho may silently disappear.
v=date(2026,9,25)
for label,expiry in [
    ("Aug26",date(2026,8,1)),
    ("Sep26",date(2026,9,1)),
    ("Oct26",date(2026,10,1)),
    ("Nov26",date(2026,11,1)),
    ("Dec26",date(2026,12,1)),
]:
    x=full_result(v,[Bucket(label,expiry,-100000)])
    assert x["contracts"][0]=="Dec-2026"
    assert abs(sum(x["company"])+1000.0)<1e-9, (label,x["company"])
    assert x["target"][0]==-40, (label,x["target"])
print("ATLAS current-quarter/front-roll edge cases: PASS")
