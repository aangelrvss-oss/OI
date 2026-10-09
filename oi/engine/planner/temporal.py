"""BLOQUE 3 · Ventanas temporales. Estados: KNOWN_RECENT / KNOWN_OLD / UNKNOWN_DATE / CONFLICTING_DATE.
Reglas: UTC siempre (naive = UTC); fecha sin hora = 00:00 UTC; ventana inclusiva (reciente si 0 <= as_of - fecha <= ventana); fecha FUTURA = anomalía (no es un hecho observado) -> CONFLICTING_DATE salvo que otra fecha válida ya sea reciente;
que exista un evento NO lo hace reciente; fechas ilegibles sin ninguna reciente -> UNKNOWN_DATE (no se puede saber)."""
import datetime as dt

KNOWN_RECENT, KNOWN_OLD, UNKNOWN_DATE, CONFLICTING_DATE = "KNOWN_RECENT", "KNOWN_OLD", "UNKNOWN_DATE", "CONFLICTING_DATE"


def parse_time(v):
    if v is None or v == "": return None
    if isinstance(v, dt.datetime): return v.replace(tzinfo=dt.timezone.utc) if v.tzinfo is None else v.astimezone(dt.timezone.utc)
    if isinstance(v, dt.date): return dt.datetime(v.year, v.month, v.day, tzinfo=dt.timezone.utc)
    s = str(v).strip()
    try:
        if len(s) == 10: return dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc)
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d.replace(tzinfo=dt.timezone.utc) if d.tzinfo is None else d.astimezone(dt.timezone.utc)
    except ValueError:
        return None


def classify_event_dates(dates, as_of, window_days, inclusive=True):
    ref = parse_time(as_of)
    if ref is None: raise ValueError("as_of ilegible")
    win = dt.timedelta(days=window_days); ev, anomalies = [], []
    for raw in dates or []:
        t = parse_time(raw)
        if t is None: ev.append({"raw": str(raw), "class": "invalid"}); anomalies.append(f"fecha_ilegible:{raw}"); continue
        age = ref - t
        if age < dt.timedelta(0): cls = "future"; anomalies.append(f"fecha_futura:{t.isoformat()}")
        elif age < win or (inclusive and age == win): cls = "recent"
        else: cls = "old"
        ev.append({"raw": str(raw), "utc": t.isoformat(), "age_days": round(age.total_seconds() / 86400, 4), "class": cls})
    cls = {e["class"] for e in ev}
    if not ev: state = UNKNOWN_DATE
    elif "recent" in cls: state = KNOWN_RECENT
    elif "future" in cls: state = CONFLICTING_DATE
    elif "invalid" in cls: state = UNKNOWN_DATE
    else: state = KNOWN_OLD
    rec = [e["utc"] for e in ev if e["class"] == "recent"]
    return {"state": state, "window_days": window_days, "as_of": ref.isoformat(), "inclusive": inclusive, "events": ev, "anomalies": anomalies, "most_recent_in_window": max(rec) if rec else None}
