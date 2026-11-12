"""Recomendación #12 — Asignación inteligente de tickets por categoría.

GET   /api/ticket-routing/rules                   Reglas por categoría y carga de cada equipo
PATCH /api/ticket-routing/rules/{category_id}     Cambiar equipo o activar la auto-asignación
GET   /api/ticket-routing/suggest?category=Bug    Responder sugerido para una categoría
POST  /api/ticket-routing/assign/{ticket_id}      Asignar un ticket con la sugerencia
POST  /api/ticket-routing/assign-pending          Repartir todos los tickets sin responsable

Además, POST /api/support-tickets asigna solo los tickets nuevos sin responsable
cuando su categoría tiene auto_assign = true (ver support.py).
La lógica de elección vive en app/services/ticket_routing.py.
"""
import json
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.neon import get_db
from app.models_db import ActionLog, Responder, SupportTicket, TicketCategory
from app.services.ticket_routing import CLOSED, norm, open_load, plan_bulk, suggest
from .auth import require_permission
from .dependencies import get_current_user

router = APIRouter(prefix="/api/ticket-routing", tags=["Asignación de Tickets"])


async def _context(db: AsyncSession):
    categories = (await db.execute(select(TicketCategory))).scalars().all()
    responders = [
        {"id": r.responder_id, "name": r.name, "team": r.team, "email": r.email}
        for r in (await db.execute(select(Responder))).scalars().all()
    ]
    tickets = (await db.execute(select(SupportTicket))).scalars().all()
    load = open_load([{"responder_id": t.responder_id, "status": t.status} for t in tickets])
    category_teams = {c.name: c.team for c in categories if c.is_active}
    return categories, responders, tickets, load, category_teams


def _log(db: AsyncSession, user: dict, entity_type: str, entity_id: str, name: str | None, changes: dict) -> None:
    db.add(ActionLog(
        log_id=f"log-{uuid.uuid4().hex[:10]}",
        admin_user_id=user.get("user_id", "unknown"),
        admin_email=user.get("email", "unknown"),
        action="UPDATE",
        entity_type=entity_type,
        entity_id=entity_id,
        entity_name=name,
        changes=json.dumps(changes, ensure_ascii=False, default=str),
    ))


def _apply(ticket: SupportTicket, responder_id: str, responder_name: str) -> None:
    ticket.responder_id = responder_id
    ticket.responder_name = responder_name
    if ticket.status == "Sent":   # con dueño pasa a "Open", igual que al crear con responder
        ticket.status = "Open"


async def auto_assign_responder(db: AsyncSession, category: str) -> Responder | None:
    """Usado por POST /api/support-tickets: responder elegido si la categoría tiene auto-asignación."""
    cat = (await db.execute(select(TicketCategory).where(TicketCategory.name == category))).scalars().first()
    if not cat or not cat.is_active or not cat.auto_assign or not cat.team:
        return None
    _, responders, _, load, category_teams = await _context(db)
    s = suggest(category, category_teams, responders, load)
    if s["status"] != "ok":
        return None
    return (await db.execute(
        select(Responder).where(Responder.responder_id == s["responder"]["id"])
    )).scalars().first()


@router.get("/rules", summary="Reglas de asignación por categoría")
async def list_rules(db: AsyncSession = Depends(get_db), _user=Depends(get_current_user)):
    categories, responders, tickets, load, category_teams = await _context(db)
    unassigned = [t for t in tickets if not t.responder_id and t.status != CLOSED]

    rules = []
    for c in sorted(categories, key=lambda c: (not c.is_active, c.name.lower())):
        team_members = [r for r in responders if c.team and norm(r["team"]) == norm(c.team)]
        s = suggest(c.name, category_teams, responders, load) if c.is_active else {"status": "unknown", "responder": None}
        rules.append({
            "category_id": c.category_id,
            "category": c.name,
            "team": c.team,
            "auto_assign": bool(getattr(c, "auto_assign", False)),
            "is_active": bool(c.is_active),
            "responders": sorted(
                [{**r, "open_tickets": load.get(r["id"], 0)} for r in team_members],
                key=lambda r: (r["open_tickets"], norm(r["name"])),
            ),
            "next_responder": s["responder"]["name"] if s.get("responder") else None,
            "status": s["status"],
            "unassigned_open": sum(1 for t in unassigned if norm(t.category) == norm(c.name)),
        })

    teams = sorted({r["team"] for r in responders if r["team"]})
    return {
        "status": "success",
        "data": {
            "rules": rules,
            "teams": teams,
            "unassigned_total": len(unassigned),
        },
    }


class RulePatch(BaseModel):
    team: Optional[str] = Field(None, max_length=60)
    auto_assign: Optional[bool] = None


@router.patch("/rules/{category_id}", summary="Editar la regla de una categoría",
              dependencies=[Depends(require_permission("tickets.manage"))])
async def update_rule(body: RulePatch, category_id: str = Path(...),
                      db: AsyncSession = Depends(get_db), user: dict = Depends(get_current_user)):
    cat = (await db.execute(select(TicketCategory).where(TicketCategory.category_id == category_id))).scalars().first()
    if not cat:
        raise HTTPException(status_code=404, detail="Categoría no encontrada")
    before = {"team": cat.team, "auto_assign": cat.auto_assign}
    if body.team is not None:
        cat.team = body.team.strip() or None
    if body.auto_assign is not None:
        if body.auto_assign and not cat.team:
            raise HTTPException(status_code=422, detail="Define un equipo antes de activar la asignación automática.")
        cat.auto_assign = body.auto_assign
    cat.updated_at = datetime.utcnow()
    db.add(cat)
    _log(db, user, "ticket_category", cat.category_id, cat.name,
         {"before": before, "after": {"team": cat.team, "auto_assign": cat.auto_assign}})
    await db.commit()
    return {"status": "success", "data": {"category_id": cat.category_id, "team": cat.team, "auto_assign": cat.auto_assign}}


@router.get("/suggest", summary="Responder sugerido para una categoría")
async def suggest_for_category(category: str = Query(..., min_length=1),
                               db: AsyncSession = Depends(get_db), _user=Depends(get_current_user)):
    _, responders, _, load, category_teams = await _context(db)
    return {"status": "success", "data": suggest(category, category_teams, responders, load)}


@router.post("/assign/{ticket_id}", summary="Asignar un ticket con la sugerencia",
             dependencies=[Depends(require_permission("tickets.manage"))])
async def assign_one(ticket_id: str = Path(...), force: bool = Query(False),
                     db: AsyncSession = Depends(get_db), user: dict = Depends(get_current_user)):
    ticket = (await db.execute(select(SupportTicket).where(SupportTicket.ticket_id == ticket_id))).scalars().first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")
    if ticket.status == CLOSED:
        raise HTTPException(status_code=422, detail="El ticket está cerrado.")
    if ticket.responder_id and not force:
        raise HTTPException(status_code=409, detail="El ticket ya tiene responsable. Usa force=true para reasignar.")

    _, responders, _, load, category_teams = await _context(db)
    if ticket.responder_id:   # al reasignar no se cuenta la carga del propio ticket
        load[ticket.responder_id] = max(load.get(ticket.responder_id, 1) - 1, 0)
    s = suggest(ticket.category, category_teams, responders, load)
    if s["status"] != "ok":
        raise HTTPException(status_code=422, detail={
            "unknown": "La categoría del ticket no existe o está inactiva.",
            "no_team": "La categoría del ticket no tiene equipo configurado.",
            "no_responders": f"El equipo '{s['team']}' no tiene responders.",
        }[s["status"]])

    before = {"responder_id": ticket.responder_id, "responder_name": ticket.responder_name, "status": ticket.status}
    r = s["responder"]
    _apply(ticket, r["id"], r["name"])
    db.add(ticket)
    _log(db, user, "support_ticket", ticket.ticket_id, ticket.title,
         {"before": before, "after": {"responder_id": r["id"], "responder_name": r["name"],
                                      "status": ticket.status, "rule": f"{ticket.category} → {s['team']}"}})
    await db.commit()
    return {"status": "success", "data": {"ticket_id": ticket.ticket_id, "responder_id": r["id"],
                                          "responder_name": r["name"], "team": s["team"], "status": ticket.status}}


@router.post("/assign-pending", summary="Repartir los tickets sin responsable",
             dependencies=[Depends(require_permission("tickets.manage"))])
async def assign_pending(db: AsyncSession = Depends(get_db), user: dict = Depends(get_current_user)):
    _, responders, tickets, load, category_teams = await _context(db)
    pending = [t for t in tickets if not t.responder_id and t.status != CLOSED]
    plan, skipped = plan_bulk(
        [{"ticket_id": t.ticket_id, "category": t.category,
          "reported_at": t.reported_at.isoformat() if t.reported_at else ""} for t in pending],
        category_teams, responders, load,
    )
    by_id = {t.ticket_id: t for t in pending}
    for a in plan:
        t = by_id[a["ticket_id"]]
        _apply(t, a["responder_id"], a["responder_name"])
        db.add(t)
    if plan:
        _log(db, user, "support_ticket", "bulk", "Asignación automática de pendientes",
             {"after": {"assigned": plan, "skipped": skipped}})
    await db.commit()
    return {"status": "success", "data": {"assigned": plan, "skipped": skipped}}
