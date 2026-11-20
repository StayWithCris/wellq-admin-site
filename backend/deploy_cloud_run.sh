#!/usr/bin/env bash
# =============================================================================
# Despliegue oficial en Google Cloud Run — WellQ Admin Backend
# =============================================================================
# Configuración oficial de costo mínimo (Opción A):
# - max-instances=1: coherencia estricta del estado en RAM (buffer, histograma, SSE)
# - min-instances=0: escala a cero sin tráfico (hace que document.visibilityState ahorre dinero real)
# - cpu-throttling: CPU asignada solo durante peticiones activas (cero costo ocioso 24/7)
# =============================================================================

set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-wellq-admin-backend}"
REGION="${REGION:-us-central1}"
PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project)}"

echo "==> Desplegando ${SERVICE_NAME} en Google Cloud Run (Región: ${REGION}, Proyecto: ${PROJECT_ID})..."

gcloud run deploy "${SERVICE_NAME}" \
  --source . \
  --platform managed \
  --region "${REGION}" \
  --project "${PROJECT_ID}" \
  --allow-unauthenticated \
  --port 8080 \
  --max-instances 1 \
  --min-instances 0 \
  --cpu-throttling \
  --timeout 300

echo "==> Despliegue finalizado con éxito."
