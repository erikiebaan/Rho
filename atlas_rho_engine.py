from __future__ import annotations
from dataclasses import dataclass
from datetime import date
import math
CONTRACT_DV01=25.0
def add_months(d,months):
    y=d.year+(d.month-1+months)//12; m=(d.month-1+months)%12+1
    return date(y,m,1)
def qmonth_ceil(d):
    m=((d.month+2)//3)*3
    return date(d.year,m,1)
def first_contract(valuation):
    return qmonth_ceil(add_months(valuation,1))
def excel_round(x):
    return int(math.floor(x+0.5)) if x>=0 else int(math.ceil(x-0.5))
@dataclass
class Bucket:
    name:str
    expiry:date
    rho:float
DEFAULT_EXECUTION={"outright_spread_bp":0.5,"pack_spread_bp":0.5,"bundle_2y_spread_bp":0.5,"bundle_3y_spread_bp":0.625,"bundle_4y_spread_bp":0.625,"bundle_5y_spread_bp":0.625,"bundle_6y_spread_bp":0.625,"outright_exchange_fee":0.0,"outright_clearing_fee":0.0,"outright_broker_fee":0.0,"strategy_exchange_fee":0.0,"strategy_clearing_fee":0.0,"strategy_broker_fee":0.0}
def strategy_name(L): return "Pack" if L==4 else f"{L//4}Y Bundle"
def strategy_universe(contracts):
    out=[]
    for L in (4,8,12,16,20,24):
        for st in range(max(0,len(contracts)-L+1)):
            legs=contracts[st:st+L]
            if all(((legs[i+1].year-legs[i].year)*12+legs[i+1].month-legs[i].month)==3 for i in range(L-1)):
                out.append({"name":strategy_name(L),"start":st,"length":L,"legs":tuple(legs)})
    return out
def quoted_spread(name,L,a):
    if name=="Outright": return float(a["outright_spread_bp"])
    if name=="Pack": return float(a["pack_spread_bp"])
    return float(a[f"bundle_{L//4}y_spread_bp"])
def order_cost(order,a):
    name,st,L,q=order; qty=abs(int(q))
    spread=qty*(quoted_spread(name,L,a)/2.0)*CONTRACT_DV01
    keys=("outright_exchange_fee","outright_clearing_fee","outright_broker_fee") if name=="Outright" else ("strategy_exchange_fee","strategy_clearing_fee","strategy_broker_fee")
    return spread+qty*sum(float(a[k]) for k in keys)
def calculate(valuation,buckets):
    front=first_contract(valuation); maxh=max([qmonth_ceil(b.expiry) for b in buckets]+[front])
    contracts=[]; d=front
    while d<=maxh: contracts.append(d); d=add_months(d,3)
    company={d:0.0 for d in contracts}
    for b in buckets:
        # Every rho bucket must be allocated to available hedge contracts.
        # If the rho month is in/past the current quarter after that quarter's
        # future has rolled, carry it into the front available quarterly future.
        horizon=max(front,qmonth_ceil(b.expiry))
        eligible=[d for d in contracts if d<=horizon]
        if not eligible:
            raise ValueError(f"No available hedge contract for rho bucket {b.name}")
        alloc=(b.rho/100.0)/len(eligible)
        for d in eligible: company[d]+=alloc
    targets={d:excel_round(company[d]/CONTRACT_DV01) for d in contracts}
    return contracts,company,targets
def optimize_exact(req,contracts,assumptions=None):
    a=dict(DEFAULT_EXECUTION); a.update(assumptions or {})
    import numpy as np
    from scipy.optimize import milp,LinearConstraint,Bounds
    from scipy.sparse import lil_matrix
    req=np.asarray([int(x) for x in req],dtype=float); n=len(req)
    inst=[("Outright",i,1) for i in range(n)]+[(s["name"],s["start"],s["length"]) for s in strategy_universe(contracts)]
    m=len(inst); c=np.zeros(2*m); A=lil_matrix((n,2*m),dtype=float)
    for j,(name,st,L) in enumerate(inst):
        c[j]=c[m+j]=order_cost((name,st,L,1),a)
        for i in range(st,st+L): A[i,j]=1; A[i,m+j]=-1
    r=milp(c=c,integrality=np.ones(2*m),bounds=Bounds(np.zeros(2*m),np.full(2*m,np.inf)),constraints=LinearConstraint(A.tocsr(),req,req),options={"presolve":True})
    if not r.success or r.x is None: raise RuntimeError(r.message)
    orders=[]
    for j,(name,st,L) in enumerate(inst):
        q=int(round(r.x[j]-r.x[m+j]))
        if q: orders.append((name,st,L,q))
    recon=[0]*n
    for name,st,L,q in orders:
        for i in range(st,st+L): recon[i]+=q
    if recon!=[int(x) for x in req]: raise RuntimeError("Exact reconciliation failed")
    return sorted(orders,key=lambda x:(x[1],x[2],x[0]))
def full_result(valuation,buckets,current=None,assumptions=None):
    a=dict(DEFAULT_EXECUTION); a.update(assumptions or {})
    contracts,company,targets=calculate(valuation,buckets); current=current or {}
    cur=[int(current.get(d.strftime("%b-%Y"),0)) for d in contracts]
    target=[targets[d] for d in contracts]; req=[target[i]-cur[i] for i in range(len(contracts))]
    exact=[("Outright",i,1,q) for i,q in enumerate(req) if q]; best=optimize_exact(req,contracts,a)
    residual=sum(company[d]-targets[d]*CONTRACT_DV01 for d in contracts)
    return {"valuation":valuation.isoformat(),"company_rho":sum(b.rho for b in buckets),"company_dv01":sum(b.rho for b in buckets)/100,"target_net":sum(target),"parallel_residual_dv01":residual,"contracts":[d.strftime("%b-%Y") for d in contracts],"company":[company[d] for d in contracts],"target":target,"current":cur,"trade":req,"exact_orders":exact,"best_orders":best,"exact_cost":sum(order_cost(o,a) for o in exact),"best_cost":sum(order_cost(o,a) for o in best),"saving":sum(order_cost(o,a) for o in exact)-sum(order_cost(o,a) for o in best)}
