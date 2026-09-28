"""ATLAS RHO destructive regression / attack tests.

Purpose: attack software behaviour without changing the locked hedge methodology.
Run: python test_atlas_rho_attack.py
"""
from __future__ import annotations
from datetime import date
from pathlib import Path
import random
import math
import numpy as np

ROOT=Path(__file__).resolve().parent
APP=ROOT/"atlas_rho_mobile_v0_8.py"

# Load only engine definitions; do not launch the Streamlit UI.
src=APP.read_text(encoding="utf-8").split("st.set_page_config",1)[0]
ns={"__file__":str(APP),"__name__":"atlas_rho_mobile_engine_test"}
exec(compile(src,str(APP),"exec"),ns)

from atlas_rho_engine import Bucket, calculate

VALUATION=ns["VALUATION"]
parse_month=ns["parse_month"]
fmt_month=ns["fmt_month"]
add_months=ns["add_months"]
locked_v17_base=ns["locked_v17_base"]
analysed_target_and_lots=ns["analysed_target_and_lots"]
optimize_exact_execution=ns["optimize_exact_execution"]
EXEC_DEFAULTS=ns["EXEC_DEFAULTS"]
engine_integrity_check=ns["engine_integrity_check"]
execution_integrity_check=ns["execution_integrity_check"]
evaluate=ns["evaluate"]

def assert_true(cond,msg):
    if not cond:
        raise AssertionError(msg)

def mobile_vs_canonical(exposures):
    qs,_,lots,_=locked_v17_base(exposures,VALUATION)
    buckets=[Bucket(fmt_month(d),d,float(r)) for d,r in exposures]
    cq,_,ct=calculate(VALUATION,buckets)
    assert_true(qs==cq,f"contract strip mismatch: {qs} != {cq}")
    # Mobile convention: + = SELL. Canonical engine target uses the opposite signed representation.
    for q in qs:
        assert_true(int(lots[q])==-int(ct[q]),f"V17 parity {q}: mobile={lots[q]} canonical={ct[q]}")

def random_exposure(rng):
    # Include already-rolled/current-quarter months and three years forward.
    month=add_months(date(VALUATION.year,VALUATION.month,1),rng.randint(-3,42))
    # Integer euros avoids meaningless floating noise while still hitting awkward rounding.
    rho=float(rng.randint(-2_500_000,2_500_000))
    return month,rho

def attack_engine_random(n=25_000,seed=20260928):
    rng=random.Random(seed)
    for k in range(n):
        count=rng.randint(1,12)
        exposures=[random_exposure(rng) for _ in range(count)]
        mobile_vs_canonical(exposures)

        contracts,target,lots=analysed_target_and_lots(exposures,VALUATION)
        assert_true(len(contracts)==len(lots)==len(target),"Analysed length mismatch")
        assert_true(np.isfinite(target).all(),"Analysed produced non-finite target")
        expected=int(round(float(np.sum(target))/ns["KR01_PER_FUTURE"]))
        assert_true(int(np.sum(lots))==expected,
                    f"Analysed net lots not conserved: {int(np.sum(lots))} != {expected}")

def attack_front_roll_edges():
    for m in (-6,-3,-2,-1,0,1,2,3,4):
        d=add_months(date(VALUATION.year,VALUATION.month,1),m)
        for rho in (-1_250_000.0,-100_000.0,100_000.0,1_250_000.0):
            mobile_vs_canonical([(d,rho)])

def attack_zero_and_signs():
    months=[parse_month(x) for x in ("Nov-26","Jan-27","Jun-27","Dec-27","Feb-28","Sep-28","Dec-28")]
    for value in (0.0,100_000.0,-100_000.0,10_000_000.0,-10_000_000.0):
        exposures=[(d,value) for d in months]
        mobile_vs_canonical(exposures)
        contracts,target,lots=analysed_target_and_lots(exposures,VALUATION)
        assert_true(np.isfinite(target).all(),"non-finite target in sign/zero attack")
        if value==0:
            assert_true(not np.any(lots),"zero rho created Analysed hedge")

def attack_execution(n=300,seed=731):
    rng=random.Random(seed)
    default=[(parse_month(m),v) for m,v in ns["DEFAULT_EXPOSURE"]]
    contracts,_,_=analysed_target_and_lots(default,VALUATION)
    regimes=[
        dict(EXEC_DEFAULTS),
        {**EXEC_DEFAULTS,"outright_spread_bp":5.0,"pack_spread_bp":0.125,
         "bundle_2y_spread_bp":0.125,"bundle_3y_spread_bp":0.25,
         "bundle_4y_spread_bp":0.25,"bundle_5y_spread_bp":0.25,"bundle_6y_spread_bp":0.25},
        {**EXEC_DEFAULTS,"outright_spread_bp":0.125,"pack_spread_bp":50.0,
         "bundle_2y_spread_bp":50.0,"bundle_3y_spread_bp":50.0,
         "bundle_4y_spread_bp":50.0,"bundle_5y_spread_bp":50.0,"bundle_6y_spread_bp":50.0},
        {**EXEC_DEFAULTS,"outright_fee":4.25,"strategy_fee":1.10},
    ]
    for k in range(n):
        req=[rng.randint(-300,300) for _ in contracts]
        # Force sparse and zero-heavy books regularly.
        if k%4==0:
            req=[q if i%3==0 else 0 for i,q in enumerate(req)]
        a=regimes[k%len(regimes)]
        ex=optimize_exact_execution(contracts,req,a)
        assert_true(ex["reconstructed"]==req,"execution changed quarterly hedge")
        assert_true(abs(ex["difference_dv01"])<1e-12,"execution Curve Delta is not zero")
        assert_true(ex["best_cost"]<=ex["reference_cost"]+1e-7,
                    "optimizer chose execution more expensive than all-outright")

def attack_rounding():
    er=ns["excel_round"]
    cases={0.49:0,0.5:1,1.5:2,-0.49:0,-0.5:-1,-1.5:-2}
    for x,want in cases.items():
        assert_true(er(x)==want,f"Excel rounding {x}: {er(x)} != {want}")

def attack_blank_mobile_cell():
    # Reproduces the iPhone failure mode: a deleted NumberColumn cell arrives as None.
    months=tuple(m for m,_ in ns["DEFAULT_EXPOSURE"])
    values=tuple(None if i in (0,3) else v for i,(_,v) in enumerate(ns["DEFAULT_EXPOSURE"]))
    r=evaluate(months,values)
    assert_true(r["decision"] in ("BASE","ANALYSED"),"blank-cell evaluation produced invalid decision")
    assert_true(all(math.isfinite(float(x)) for x in r["base"]+r["analysed"]),
                "blank cell produced non-finite hedge")

def main():
    print("ATLAS RHO ATTACK TEST")
    engine_integrity_check()
    execution_integrity_check()
    print("PASS  golden engine + execution locks")

    attack_rounding()
    print("PASS  Excel half-away-from-zero rounding")

    attack_front_roll_edges()
    print("PASS  front-roll/current-quarter/past-month edges")

    attack_zero_and_signs()
    print("PASS  zero / all-positive / all-negative / extreme rho")

    attack_blank_mobile_cell()
    print("PASS  iPhone blank-cell / None regression")

    attack_engine_random()
    print("PASS  25,000 randomized portfolios: V17 parity + Analysed conservation")

    attack_execution()
    print("PASS  300 randomized exact-execution attacks across cost regimes")

    print("ALL ATLAS RHO ATTACK TESTS PASSED")

if __name__=="__main__":
    main()
