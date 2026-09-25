"""ATLAS RHO historical EURIBOR curve helpers."""
import pandas as pd

HORIZONS={"NOW":0,"+1W":5,"+1M":21,"+3M":63,"+6M":126}

def normalize_history(df):
    x=df.copy()
    cols={str(c).strip().lower():c for c in x.columns}
    d=next((cols[k] for k in ("date","trade_date","day") if k in cols),None)
    p=next((cols[k] for k in ("close","price","last","settle","settlement") if k in cols),None)
    m=next((cols[k] for k in ("contract","expiry","maturity","requested_expiry","local_symbol") if k in cols),None)
    if d is None or p is None or m is None:
        raise ValueError("History CSV needs date, contract/expiry and close/price/settle.")
    x=x[[d,m,p]].rename(columns={d:"date",m:"contract",p:"price"})
    x["date"]=pd.to_datetime(x["date"],errors="coerce").dt.normalize()
    x["price"]=pd.to_numeric(x["price"],errors="coerce")
    x=x.dropna().drop_duplicates(["date","contract"],keep="last").sort_values(["date","contract"])
    x["implied_rate"]=100.0-x["price"]
    return x

def available_dates(df):
    return list(pd.DatetimeIndex(df["date"].drop_duplicates()).sort_values())

def comparison(df, selected_date):
    dates=available_dates(df)
    if not dates: return {}
    target=pd.Timestamp(selected_date).normalize()
    base=min(dates,key=lambda d:abs((d-target).days))
    i=dates.index(base); out={}
    for label,offset in HORIZONS.items():
        j=min(i+offset,len(dates)-1)
        d=dates[j]
        z=df[df["date"]==d][["contract","implied_rate"]].copy()
        z["series"]=label
        z["actual_date"]=d
        out[label]=z
    return out
