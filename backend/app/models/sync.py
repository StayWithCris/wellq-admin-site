"""SQLModel table definitions for the WellQ Admin Console."""

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, ForeignKey, Integer, PrimaryKeyConstraint, Text
from sqlmodel import Field, SQLModel

class ClinicianSummary(SQLModel, table=True):
    """
    Resumen agregado de clínicos por clínica.

    Fuente MongoDB: colección `clinicians`
    Lógica de sync:
      - clinic_id        → clinicians.clinic_id (mapeado como string)
      - total_clinicians → COUNT(docs) agrupado por clinic_id
      - active_clinicians → COUNT WHERE state = "active"
      - specialties      → specialties[] serializado como JSON string
      - recorded_at      → timestamp del momento de sync

    Nota: los campos individuales (first_name, last_name, contact, ids,
    metadata) se ignoran — esta tabla guarda el agregado, no replica
    cada clínico.
    """
    __tablename__ = "clinician_summaries"

    id: Optional[int]           = Field(default=None, primary_key=True)
    clinic_id: str              = Field(index=True)          # → clinics.clinic_id (también se puede join por mongo_clinic_id)
    total_clinicians: int       = Field(default=0)           # COUNT total de docs en clinicians con ese clinic_id
    active_clinicians: int      = Field(default=0)           # COUNT WHERE state = "active"
    specialties: Optional[str]  = Field(default=None)        # JSON array: '["Kinesiología","Traumatología"]'
    recorded_at: datetime       = Field(default_factory=datetime.utcnow)  # timestamp de sync

class PatientHealthSummary(SQLModel, table=True):
    """
    Resumen de salud clínica de pacientes agrupado por clínica.

    Fuente MongoDB: colección `patients` campo `status`
    Valores posibles de status: stable | declining | at_risk | improving
    (también disponible en historial_medico.estado_act.est_act_nom como fuente alternativa)

    Lógica de sync (aggregation pipeline):
      db.patients.aggregate([
        { $match: { clinic_ids: ObjectId(mongo_clinic_id) } },
        { $group: {
            _id: None,
            total_patients: { $sum: 1 },
            at_risk:   { $sum: { $cond: [{ $eq: ["$status","at_risk"]   }, 1, 0] } },
            declining: { $sum: { $cond: [{ $eq: ["$status","declining"] }, 1, 0] } },
            stable:    { $sum: { $cond: [{ $eq: ["$status","stable"]    }, 1, 0] } },
            improving: { $sum: { $cond: [{ $eq: ["$status","improving"] }, 1, 0] } },
        }}
      ])

    Nota: patients.clinic_ids es un array → se usa $match con igualdad directa
    (MongoDB evalúa automáticamente si el valor está en el array).
    Los totales (at_risk + declining + stable + improving) deben coincidir
    con total_patients y con patients_used en clinics.
    """
    __tablename__ = "patient_health_summaries"

    id: Optional[int]      = Field(default=None, primary_key=True)
    clinic_id: str         = Field(index=True)    # → clinics.clinic_id
    total_patients: int    = Field(default=0)     # debe coincidir con clinics.patients_used
    at_risk: int           = Field(default=0)     # patients.status = "at_risk"
    declining: int         = Field(default=0)     # patients.status = "declining"
    stable: int            = Field(default=0)     # patients.status = "stable"
    improving: int         = Field(default=0)     # patients.status = "improving"
    recorded_at: datetime  = Field(default_factory=datetime.utcnow)  # timestamp de sync

class SyncRun(SQLModel, table=True):
    """Historial de ejecuciones de sincronización (Recomendación #7).

    Cada fila representa una sincronización manual o programada. `entities`
    guarda un JSON con los registros tocados por fuente, por ejemplo
    ``{"clinics": 8, "patients": 45}``, para poder auditar qué se actualizó.

    Estado: running | completed | failed. Una fila en `running` durante más de
    10 minutos se considera huérfana y se cierra como `failed`.
    """
    __tablename__ = "sync_runs"

    id: Optional[int]            = Field(default=None, primary_key=True)
    run_id: str                  = Field(unique=True, index=True)   # "sync-{uuid4().hex[:12]}"
    trigger: str                 = Field(default="manual")          # manual | scheduled
    status: str                  = Field(default="running")         # running | completed | failed
    frequency_hours: int         = Field(default=24)
    entities: Optional[str]      = Field(default=None, sa_column=Column(Text))  # JSON
    created_by: Optional[str]    = Field(default=None)
    error: Optional[str]         = Field(default=None, sa_column=Column(Text))
    started_at: datetime         = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = Field(default=None)
