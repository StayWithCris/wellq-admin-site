"""Recomendación #11 — Reportes programados (área Analítica).

``ScheduledReport`` guarda la programación de cada reporte y
``ScheduledReportRun`` el historial de envíos. Todo vive en Neon (no en
memoria) para que funcione con Cloud Run escalando a cero.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, Text
from sqlmodel import Field, SQLModel


class ScheduledReport(SQLModel, table=True):
    __tablename__ = "scheduled_reports"

    id: Optional[int]               = Field(default=None, primary_key=True)
    report_id: str                  = Field(unique=True, index=True)          # "REP-xxxxxxxx"
    name: str                       = Field(max_length=120)
    description: Optional[str]      = Field(default=None, sa_column=Column(Text))
    frequency: str                  = Field()                                 # daily | weekly | monthly
    day_of_week: Optional[int]      = Field(default=None)                     # 0=lunes … 6=domingo (weekly)
    day_of_month: Optional[int]     = Field(default=None)                     # 1..28 (monthly)
    send_hour_utc: int              = Field(default=12)                       # 0..23
    sections: str                   = Field(sa_column=Column(Text))           # JSON list
    channels: str                   = Field(sa_column=Column(Text))           # JSON list: email / slack
    recipients: str                 = Field(sa_column=Column(Text))           # JSON list de emails
    include_csv: bool               = Field(default=True)
    is_active: bool                 = Field(default=True, index=True)
    last_run_at: Optional[datetime] = Field(default=None)
    next_run_at: Optional[datetime] = Field(default=None, index=True)
    created_by: str                 = Field(default="system")
    updated_by: Optional[str]       = Field(default=None)
    created_at: datetime            = Field(default_factory=datetime.utcnow)
    updated_at: datetime            = Field(default_factory=datetime.utcnow)


class ScheduledReportRun(SQLModel, table=True):
    __tablename__ = "scheduled_report_runs"

    id: Optional[int]               = Field(default=None, primary_key=True)
    run_id: str                     = Field(unique=True, index=True)          # "REPRUN-xxxxxxxx"
    report_id: str                  = Field(index=True)                       # FK lógica a scheduled_reports
    report_name: str                = Field()
    trigger: str                    = Field()                                 # scheduled | manual | cron_webhook
    triggered_by: str               = Field(default="scheduler")             # "scheduler" o email del admin
    status: str                     = Field(default="sent")                   # sent | partial | failed | skipped
    channels_summary: Optional[str] = Field(default=None, sa_column=Column(Text))  # JSON {"email": "sent"}
    recipients_count: int           = Field(default=0)
    error_detail: Optional[str]     = Field(default=None, sa_column=Column(Text))
    period_label: Optional[str]     = Field(default=None)
    created_at: datetime            = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = Field(default=None)
