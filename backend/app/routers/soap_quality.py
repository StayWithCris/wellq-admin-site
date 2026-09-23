from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from typing import List, Optional

from app.db.neon import get_db
from app.models_db import SoapQualityMetric

router = APIRouter(
    prefix="/api/ai/soap-quality",
    tags=["AI Quality Metrics"]
)

@router.get("/")
async def get_soap_quality_metrics(db: AsyncSession = Depends(get_db)):
    """Obtiene el historial y la métrica más reciente de calidad SOAP desde Neon."""
    try:
        statement = select(SoapQualityMetric).order_by(desc(SoapQualityMetric.recorded_at))
        result = await db.execute(statement)
        records = result.scalars().all()
        
        if not records:
            return {"data": [], "latest": None}
            
        latest = records[0]
        return {
            "data": records,
            "latest": {
                "period": latest.period,
                "total_notes_generated": latest.total_notes_generated,
                "acceptance_rate_percentage": latest.acceptance_rate_percentage,
                "edits_required_percentage": latest.edits_required_percentage,
                "average_time_saved_minutes_per_note": latest.average_time_saved_minutes_per_note,
                "common_corrections": latest.common_corrections,
                "recorded_at": latest.recorded_at.isoformat() if latest.recorded_at else None
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al obtener métricas SOAP: {str(e)}")