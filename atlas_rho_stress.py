"""ATLAS RHO first-order curve stress engine.

All P&L is computed from residual quarterly DV01 after the proposed hedge.
Positive shock means rates higher in basis points.
"""

SCENARIOS = (
    "Parallel +100",
    "Parallel +50",
    "Parallel -50",
    "Parallel -100",
    "Front +50",
    "Back +50",
    "Bear steepener",
    "Bull flattener",
)

def _ramp(a, b, n):
    if n <= 1:
        return [float(b)] * n
    return [float(a) + (float(b) - float(a)) * i / (n - 1) for i in range(n)]

def residual_dv01(company_dv01, target_contracts, eur_per_bp=25.0):
    """Residual DV01 = company DV01 minus hedge sensitivity.

    target_contracts follows ATLAS sign convention: negative = short futures.
    A short futures hedge has positive rates-DV01, hence company + target*25.
    """
    if len(company_dv01) != len(target_contracts):
        raise ValueError("company_dv01 and target_contracts length mismatch")
    return [float(c) - float(q) * eur_per_bp for c, q in zip(company_dv01, target_contracts)]

def shock_vectors(n):
    return {
        "Parallel +100": [100.0] * n,
        "Parallel +50": [50.0] * n,
        "Parallel -50": [-50.0] * n,
        "Parallel -100": [-100.0] * n,
        "Front +50": _ramp(50.0, 0.0, n),
        "Back +50": _ramp(0.0, 50.0, n),
        "Bear steepener": _ramp(-25.0, 50.0, n),
        "Bull flattener": _ramp(25.0, -50.0, n),
    }

def stress_results(company_dv01, target_contracts):
    residual = residual_dv01(company_dv01, target_contracts)
    vectors = shock_vectors(len(residual))
    out = {}
    for name, shocks in vectors.items():
        pnl = sum(dv01 * bp for dv01, bp in zip(residual, shocks))
        out[name] = 0.0 if abs(pnl) < 0.005 else pnl
    return {"residual_dv01": residual, "scenarios": out}

def validate_parallel(company_dv01, target_contracts, tolerance=1e-8):
    r = residual_dv01(company_dv01, target_contracts)
    return abs(sum(r)) <= tolerance
