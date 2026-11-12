"""Mejora #1 — Carga e historial de MRR: cálculos puros (sin base de datos).

Se separan del router para poder probarlos sin FastAPI ni Neon.
"""
from datetime import date, datetime, timedelta

# Mismas abreviaturas que usa GET /api/financials/mrr/snapshots
MONTH_ABBR_ES = {
    1: "Ene", 2: "Feb", 3: "Mar", 4: "Abr", 5: "May", 6: "Jun",
    7: "Jul", 8: "Ago", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dic",
}

BREAKDOWN_FIELDS = ("total_mrr", "new_business", "expansion", "contraction", "churn")


def month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    """Primer y último instante del mes (UTC, sin zona)."""
    start = datetime(year, month, 1)
    nxt = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    return start, nxt - timedelta(seconds=1)


def previous_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def default_period(today: date) -> tuple[int, int]:
    """El mes que normalmente se cierra: el anterior al actual."""
    return previous_month(today.year, today.month)


def validate_period(year: int, month: int, today: date) -> None:
    """Solo se pueden cerrar meses ya terminados. Lanza ValueError si no."""
    if not 1 <= month <= 12:
        raise ValueError("El mes debe estar entre 1 y 12.")
    if year < 2000:
        raise ValueError("El año no es válido.")
    if (year, month) >= (today.year, today.month):
        raise ValueError("Solo se pueden cerrar meses que ya terminaron.")


def growth_pct(total: float, prev_total: float | None) -> float:
    if not prev_total or prev_total <= 0:
        return 0.0
    return round((total - prev_total) / prev_total * 100, 1)


def build_values(calculated: dict, manual: dict | None) -> dict:
    """
    Devuelve los valores a guardar.
    - manual = None  → se usa el cálculo automático tal cual.
    - manual = {...} → cada campo ingresado reemplaza al calculado.
    El MRR retenido se recalcula como total - expansión si no se ingresa,
    igual que en el cálculo automático (retenido incluye negocio nuevo).
    """
    values = {k: round(float(calculated.get(k, 0) or 0), 2) for k in BREAKDOWN_FIELDS}
    values["retained"] = round(float(calculated.get("retained", 0) or 0), 2)
    if manual is None:
        return values

    for k in BREAKDOWN_FIELDS:
        if manual.get(k) is not None:
            v = float(manual[k])
            if v < 0:
                raise ValueError(f"El campo '{k}' no puede ser negativo.")
            values[k] = round(v, 2)

    if manual.get("retained") is not None:
        r = float(manual["retained"])
        if r < 0:
            raise ValueError("El campo 'retained' no puede ser negativo.")
        values["retained"] = round(r, 2)
    else:
        values["retained"] = round(max(values["total_mrr"] - values["expansion"], 0.0), 2)

    if values["new_business"] > values["total_mrr"]:
        raise ValueError("El negocio nuevo no puede superar el MRR total.")
    return values


def merge_closed(series: list[dict], closed: list[dict]) -> list[dict]:
    """
    Reemplaza en la serie calculada los meses que tienen un cierre oficial
    y agrega los cierres de meses que la serie no cubre. Marca cada punto
    con source = "closed" o "calculated". Devuelve la serie en orden cronológico.
    """
    month_num = {v: k for k, v in MONTH_ABBR_ES.items()}
    by_key = {(c["period_month"], int(c["period_year"])): c for c in closed}
    out = []
    seen = set()
    for point in series:
        key = (point["period_month"], int(point["period_year"]))
        if key in by_key:
            c = by_key[key]
            merged = {**point, **{k: c[k] for k in (*BREAKDOWN_FIELDS, "retained", "monthly_growth_percentage") if k in c}}
            merged["source"] = "closed"
            out.append(merged)
            seen.add(key)
        else:
            out.append({**point, "source": "calculated"})
    for key, c in by_key.items():
        if key not in seen:
            out.append({
                "period_month": c["period_month"],
                "period_year": int(c["period_year"]),
                **{k: c.get(k, 0.0) for k in (*BREAKDOWN_FIELDS, "retained", "monthly_growth_percentage")},
                "source": "closed",
            })
    out.sort(key=lambda p: (int(p["period_year"]), month_num.get(p["period_month"], 0)))
    return out
