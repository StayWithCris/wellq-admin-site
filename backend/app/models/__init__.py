"""Canonical SQLModel database models split by domain."""

from app.models.organization import Clinic
from app.models.plans import Feature, Plan, PlanCatalog, PlanFeature, ClinicPlan, ScheduledChange, Discount, ClinicDiscount
from app.models.operations import Alert, Notification, IncidentNotificationDelivery, Job, AppVersion, PlatformSetting, ForceUpdateConfig
from app.models.identity import Role, Permission, RolePermission, AdminUser, PasswordResetToken, AdminSession
from app.models.analytics import KpiSnapshot, AppMetric, Invoice, ClinicUsageMetric, MrrSnapshot, AppUsageStat, FeatureAdoption, AdherenceSnapshot, CohortRetention, SoapQualityMetric, AiCostSnapshot, AiLatencyMetric, PoseAnalysisSnapshot, NeedsAttentionItem, InfrastructureCostSnapshot
from app.models.infrastructure import Server, BackgroundProcess, InfraNode
from app.models.sync import ClinicianSummary, PatientHealthSummary, SyncRun
from app.models.support import SupportTicket, Responder, TicketCategory
from app.models.audit import ImpersonateAuditLog, ActionLog

__all__ = [
    "Clinic",
    "Feature",
    "Plan",
    "PlanCatalog",
    "PlanFeature",
    "ClinicPlan",
    "ScheduledChange",
    "Discount",
    "ClinicDiscount",
    "Alert",
    "Notification",
    "IncidentNotificationDelivery",
    "Job",
    "AppVersion",
    "PlatformSetting",
    "ForceUpdateConfig",
    "Role",
    "Permission",
    "RolePermission",
    "AdminUser",
    "PasswordResetToken",
    "AdminSession",
    "KpiSnapshot",
    "AppMetric",
    "Invoice",
    "ClinicUsageMetric",
    "MrrSnapshot",
    "AppUsageStat",
    "FeatureAdoption",
    "AdherenceSnapshot",
    "CohortRetention",
    "SoapQualityMetric",
    "AiCostSnapshot",
    "AiLatencyMetric",
    "PoseAnalysisSnapshot",
    "NeedsAttentionItem",
    "InfrastructureCostSnapshot",
    "Server",
    "BackgroundProcess",
    "InfraNode",
    "ClinicianSummary",
    "PatientHealthSummary",
    "SyncRun",
    "SupportTicket",
    "Responder",
    "TicketCategory",
    "ImpersonateAuditLog",
    "ActionLog",
]
