"""
infra_costs.py — Proyector de costos de infraestructura cloud
==============================================================================
Implementa la mejora N°6 del Informe de Recomendaciones para Futuras Versiones
(eje de Infraestructura y Platform Ops).

Contexto del hallazgo
---------------------
La tabla `infrastructure_cost_snapshots` ya existía en el modelo de datos del
sistema base, con todas sus columnas definidas, pero ningún endpoint la exponía:
era una tabla huerfana. Este modulo la conecta a la API y agrega encima el
simulador de costos que pide la recomendacion.

Endpoints
---------
GET  /api/infrastructure/costs           Snapshot del periodo vigente
GET  /api/infrastructure/costs/history   Serie historica para graficar
POST /api/infrastructure/costs/project   Simulador: proyecta el costo al agregar
                                         N clinicas activas adicionales
==============================================================================
"""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field as PydanticField
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.neon import get_db
from app.models_db import InfrastructureCostSnapshot

router = APIRouter(prefix="/api/infrastructure", tags=["Infraestructura y Ops"])


# ==============================================================================
# TARIFAS UNITARIAS
# ------------------------------------------------------------------------------
# IMPORTANTE: estos valores son parametros de calibracion, no constantes fijas.
# Deben ajustarse contra la factura real de Google Cloud y Neon antes de dar el
# resultado por valido. Se exponen en la respuesta del simulador para que el
# calculo sea auditable y no una caja negra.
# ==============================================================================

TARIFAS = {
    # Google Cloud Run
    "cloud_run_vcpu_segundo_usd":    0.000024,
    "cloud_run_gib_segundo_usd":     0.0000025,
    "cloud_run_millon_requests_usd": 0.40,

    # Neon (PostgreSQL serverless)
    "neon_gb_almacenado_mes_usd":    0.35,
    "neon_hora_computo_usd":         0.16,
}

# Supuestos de consumo por clinica al mes. Tambien son calibrables: la idea es
# reemplazarlos con la medicion real una vez que la telemetria este operativa.
SUPUESTOS_POR_CLINICA = {
    "requests_mes":            45_000,   # llamadas a la API
    "vcpu_segundos_mes":       3_600,    # ~1 hora de CPU efectiva
    "gib_segundos_mes":        1_800,    # memoria reservada * tiempo
    "almacenamiento_gb":       0.8,      # datos clinicos + adjuntos
    "horas_computo_db_mes":    12,       # tiempo activo del compute de Neon
}


# ==============================================================================
# ESQUEMAS DE ENTRADA
# ==============================================================================

class ProyeccionRequest(BaseModel):
    """Parametros del simulador de costos."""

    clinicas_adicionales: int = PydanticField(
        ..., ge=0, le=10_000,
        description="Cuantas clinicas activas nuevas se quieren simular",
    )
    meses: int = PydanticField(
        6, ge=1, le=36,
        description="Horizonte de la proyeccion en meses",
    )
    # Permiten sobrescribir los supuestos por clinica sin tocar el codigo
    requests_mes_por_clinica: Optional[int] = PydanticField(default=None, ge=0)
    almacenamiento_gb_por_clinica: Optional[float] = PydanticField(default=None, ge=0)


# ==============================================================================
# HELPERS
# ==============================================================================

def _parse_breakdown(raw: Optional[str]) -> List[Dict[str, Any]]:
    """El campo `breakdown` guarda un JSON en texto. Si viene corrupto o vacio
    devolvemos lista vacia en vez de reventar el endpoint.

    Normaliza ademas las claves: los registros sembrados por `seed.py` usan
    {"service", "cost"}, mientras que el simulador produce {"servicio", "usd"}.
    Se unifica a {"servicio", "usd"} para que el frontend consuma una sola forma.
    """
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []

    if not isinstance(data, list):
        return []

    normalizado: List[Dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        servicio = item.get("servicio", item.get("service", "Sin categoria"))
        usd_raw  = item.get("usd", item.get("cost", 0))
        try:
            usd = round(float(usd_raw), 2)
        except (TypeError, ValueError):
            usd = 0.0
        entrada = {"servicio": servicio, "usd": usd}
        if "unidades" in item:
            entrada["unidades"] = item["unidades"]
        normalizado.append(entrada)
    return normalizado


def _serializar_snapshot(s: InfrastructureCostSnapshot) -> Dict[str, Any]:
    return {
        "id":                  s.id,
        "period":              s.period,
        "period_year":         s.period_year,
        "period_month":        s.period_month,
        "total_usd":           round(s.total_usd, 2),
        "budget_usd":          round(s.budget_usd, 2),
        "budget_used_percent": round(s.budget_used_percent, 2),
        "breakdown":           _parse_breakdown(s.breakdown),
        "last_updated":        s.last_updated,
    }


def _calcular_costo_mensual(n_clinicas: int,
                            requests_mes: int,
                            almacenamiento_gb: float) -> Dict[str, Any]:
    """Traduce un numero de clinicas a costo mensual estimado, desglosado por
    servicio. Devuelve tanto el total como el detalle, para que en la interfaz
    se pueda mostrar de donde sale cada dolar."""

    vcpu_seg   = n_clinicas * SUPUESTOS_POR_CLINICA["vcpu_segundos_mes"]
    gib_seg    = n_clinicas * SUPUESTOS_POR_CLINICA["gib_segundos_mes"]
    requests   = n_clinicas * requests_mes
    storage_gb = n_clinicas * almacenamiento_gb
    horas_db   = n_clinicas * SUPUESTOS_POR_CLINICA["horas_computo_db_mes"]

    costo_cpu       = vcpu_seg   * TARIFAS["cloud_run_vcpu_segundo_usd"]
    costo_memoria   = gib_seg    * TARIFAS["cloud_run_gib_segundo_usd"]
    costo_requests  = (requests / 1_000_000) * TARIFAS["cloud_run_millon_requests_usd"]
    costo_storage   = storage_gb * TARIFAS["neon_gb_almacenado_mes_usd"]
    costo_computo   = horas_db   * TARIFAS["neon_hora_computo_usd"]

    total = costo_cpu + costo_memoria + costo_requests + costo_storage + costo_computo

    return {
        "total_usd": round(total, 2),
        "desglose": [
            {"servicio": "Cloud Run — CPU",        "usd": round(costo_cpu, 2),
             "unidades": f"{vcpu_seg:,.0f} vCPU-seg"},
            {"servicio": "Cloud Run — Memoria",    "usd": round(costo_memoria, 2),
             "unidades": f"{gib_seg:,.0f} GiB-seg"},
            {"servicio": "Cloud Run — Requests",   "usd": round(costo_requests, 2),
             "unidades": f"{requests:,.0f} requests"},
            {"servicio": "Neon — Almacenamiento",  "usd": round(costo_storage, 2),
             "unidades": f"{storage_gb:,.1f} GB"},
            {"servicio": "Neon — Computo",         "usd": round(costo_computo, 2),
             "unidades": f"{horas_db:,.0f} horas"},
        ],
    }


# ==============================================================================
# ENDPOINT: GET /api/infrastructure/costs
# Descripcion: Costo de infraestructura del periodo vigente contra presupuesto
# ==============================================================================
@router.get(
    "/costs",
    summary="Costo de infraestructura del periodo vigente",
    description="Devuelve el snapshot mas reciente de costo cloud con su "
                "desglose por servicio y el porcentaje de presupuesto consumido.",
)
async def get_costos_actuales(db: AsyncSession = Depends(get_db)):
    # Operacion: tomar el snapshot mas reciente por año y mes
    result = await db.execute(
        select(InfrastructureCostSnapshot)
        .order_by(desc(InfrastructureCostSnapshot.period_year),
                  desc(InfrastructureCostSnapshot.period_month))
        .limit(1)
    )
    snapshot = result.scalars().first()

    if not snapshot:
        # Sin datos aun: respondemos 200 con estructura vacia en vez de 404,
        # para que la interfaz pueda renderizar el estado "sin registros".
        return {
            "status": "empty",
            "message": "Aun no hay snapshots de costo registrados.",
            "data": None,
        }

    datos = _serializar_snapshot(snapshot)

    # Semaforo de presupuesto, para que la UI no tenga que recalcularlo
    pct = datos["budget_used_percent"]
    datos["estado_presupuesto"] = (
        "critico" if pct >= 90 else "alerta" if pct >= 75 else "normal"
    )

    return {"status": "success", "data": datos}


# ==============================================================================
# ENDPOINT: GET /api/infrastructure/costs/history
# Descripcion: Serie historica de costos para graficar la tendencia
# ==============================================================================
@router.get(
    "/costs/history",
    summary="Serie historica de costos de infraestructura",
)
async def get_historial_costos(
    meses: int = Query(12, ge=1, le=60, description="Cuantos periodos devolver"),
    db: AsyncSession = Depends(get_db),
):
    # Operacion: traer los ultimos N periodos ordenados de mas nuevo a mas viejo
    result = await db.execute(
        select(InfrastructureCostSnapshot)
        .order_by(desc(InfrastructureCostSnapshot.period_year),
                  desc(InfrastructureCostSnapshot.period_month))
        .limit(meses)
    )
    snapshots = result.scalars().all()

    # Se invierte para que el grafico quede en orden cronologico ascendente
    serie = [_serializar_snapshot(s) for s in reversed(snapshots)]

    total_acumulado = sum(s["total_usd"] for s in serie)
    promedio = round(total_acumulado / len(serie), 2) if serie else 0.0

    return {
        "status": "success",
        "periodos": len(serie),
        "promedio_mensual_usd": promedio,
        "total_acumulado_usd": round(total_acumulado, 2),
        "data": serie,
    }


# ==============================================================================
# ENDPOINT: POST /api/infrastructure/costs/project
# Descripcion: Simulador — proyecta el costo al sumar N clinicas activas
# ==============================================================================
@router.post(
    "/costs/project",
    summary="Proyecta el costo de infraestructura al agregar clinicas",
    description="Simula el impacto en el costo mensual de incorporar un numero "
                "determinado de clinicas activas, desglosado por servicio cloud.",
)
async def proyectar_costos(
    payload: ProyeccionRequest,
    db: AsyncSession = Depends(get_db),
):
    if payload.clinicas_adicionales == 0 and payload.meses == 0:
        raise HTTPException(status_code=400, detail="Parametros de simulacion vacios")

    # Supuestos efectivos: los del request si vienen, si no los por defecto
    requests_mes = (payload.requests_mes_por_clinica
                    if payload.requests_mes_por_clinica is not None
                    else SUPUESTOS_POR_CLINICA["requests_mes"])
    storage_gb = (payload.almacenamiento_gb_por_clinica
                  if payload.almacenamiento_gb_por_clinica is not None
                  else SUPUESTOS_POR_CLINICA["almacenamiento_gb"])

    # Operacion: partir del ultimo snapshot real como linea base
    result = await db.execute(
        select(InfrastructureCostSnapshot)
        .order_by(desc(InfrastructureCostSnapshot.period_year),
                  desc(InfrastructureCostSnapshot.period_month))
        .limit(1)
    )
    base = result.scalars().first()

    costo_base_usd       = round(base.total_usd, 2) if base else 0.0
    presupuesto_usd      = round(base.budget_usd, 2) if base else 0.0
    periodo_base         = base.period if base else "sin linea base"

    # Costo incremental que agregan las clinicas simuladas
    incremental = _calcular_costo_mensual(
        payload.clinicas_adicionales, requests_mes, storage_gb
    )
    costo_proyectado = round(costo_base_usd + incremental["total_usd"], 2)

    # Serie mes a mes: las clinicas se incorporan de forma lineal en el horizonte
    serie: List[Dict[str, Any]] = []
    for mes in range(1, payload.meses + 1):
        proporcion = mes / payload.meses
        clinicas_mes = round(payload.clinicas_adicionales * proporcion)
        parcial = _calcular_costo_mensual(clinicas_mes, requests_mes, storage_gb)
        serie.append({
            "mes":                   mes,
            "clinicas_acumuladas":   clinicas_mes,
            "costo_estimado_usd":    round(costo_base_usd + parcial["total_usd"], 2),
        })

    # Alerta de presupuesto: en que mes se supera, si es que se supera
    mes_excede = next(
        (p["mes"] for p in serie
         if presupuesto_usd > 0 and p["costo_estimado_usd"] > presupuesto_usd),
        None,
    )

    return {
        "status": "success",
        "simulacion": {
            "clinicas_adicionales": payload.clinicas_adicionales,
            "meses":                payload.meses,
        },
        "linea_base": {
            "periodo":         periodo_base,
            "costo_usd":       costo_base_usd,
            "presupuesto_usd": presupuesto_usd,
        },
        "proyeccion": {
            "costo_incremental_usd": incremental["total_usd"],
            "costo_total_usd":       costo_proyectado,
            "variacion_porcentual":  (
                round((incremental["total_usd"] / costo_base_usd) * 100, 1)
                if costo_base_usd > 0 else None
            ),
            "desglose": incremental["desglose"],
        },
        "serie_mensual": serie,
        "alerta_presupuesto": {
            "excede":     mes_excede is not None,
            "mes_excede": mes_excede,
        },
        # Transparencia: el calculo queda auditable desde la propia respuesta
        "parametros_utilizados": {
            "tarifas": TARIFAS,
            "supuestos_por_clinica": {
                **SUPUESTOS_POR_CLINICA,
                "requests_mes": requests_mes,
                "almacenamiento_gb": storage_gb,
            },
            "advertencia": "Tarifas y supuestos son parametros de calibracion. "
                           "Deben validarse contra la factura real de Google Cloud "
                           "y Neon antes de usarse para decisiones de negocio.",
        },
        "generado_en": datetime.utcnow(),
    }
