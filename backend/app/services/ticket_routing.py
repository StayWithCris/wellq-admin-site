"""Mejora #12 — Asignación inteligente de tickets: reglas puras (sin base de datos).

Regla: la categoría del ticket define el equipo (ticket_categories.team).
Dentro de ese equipo se elige al responder con menos tickets abiertos;
si hay empate, el primero por nombre. Así la carga se reparte de forma pareja.
"""

CLOSED = "Closed"


def norm(text: str | None) -> str:
    return (text or "").strip().lower()


def open_load(tickets: list[dict]) -> dict[str, int]:
    """Tickets no cerrados por responder_id."""
    load: dict[str, int] = {}
    for t in tickets:
        rid = t.get("responder_id")
        if rid and t.get("status") != CLOSED:
            load[rid] = load.get(rid, 0) + 1
    return load


def pick_responder(team: str | None, responders: list[dict], load: dict[str, int]) -> dict | None:
    """Responder del equipo con menos tickets abiertos (None si el equipo no tiene responders)."""
    candidates = [r for r in responders if team and norm(r.get("team")) == norm(team)]
    if not candidates:
        return None
    best = min(candidates, key=lambda r: (load.get(r["id"], 0), norm(r.get("name"))))
    return {**best, "open_tickets": load.get(best["id"], 0)}


def suggest(category: str | None, category_teams: dict[str, str | None],
            responders: list[dict], load: dict[str, int]) -> dict:
    """
    Sugerencia para una categoría. status:
      ok             → hay responder sugerido
      unknown        → la categoría no existe o está inactiva
      no_team        → la categoría no tiene equipo configurado
      no_responders  → el equipo no tiene responders
    """
    teams = {norm(k): v for k, v in category_teams.items()}
    key = norm(category)
    if key not in teams:
        return {"status": "unknown", "team": None, "responder": None}
    team = teams[key]
    if not team:
        return {"status": "no_team", "team": None, "responder": None}
    choice = pick_responder(team, responders, load)
    if not choice:
        return {"status": "no_responders", "team": team, "responder": None}
    return {"status": "ok", "team": team, "responder": choice}


def plan_bulk(pending: list[dict], category_teams: dict[str, str | None],
              responders: list[dict], load: dict[str, int]) -> tuple[list[dict], list[dict]]:
    """
    Reparte una lista de tickets sin responsable. La carga se actualiza después
    de cada asignación para no cargar todo a la misma persona.
    Devuelve (asignados, omitidos).
    """
    load = dict(load)
    assigned, skipped = [], []
    for t in sorted(pending, key=lambda t: t.get("reported_at") or ""):
        s = suggest(t.get("category"), category_teams, responders, load)
        if s["status"] != "ok":
            skipped.append({"ticket_id": t["ticket_id"], "category": t.get("category"), "reason": s["status"]})
            continue
        r = s["responder"]
        load[r["id"]] = load.get(r["id"], 0) + 1
        assigned.append({"ticket_id": t["ticket_id"], "category": t.get("category"),
                         "responder_id": r["id"], "responder_name": r["name"], "team": s["team"]})
    return assigned, skipped
