# =============================================================================
# Despliegue oficial en Google Cloud Run — WellQ Admin Backend (PowerShell)
# =============================================================================
# Configuración oficial de costo mínimo (Opción A):
# - max-instances=1: coherencia estricta del estado en RAM (buffer, histograma, SSE)
# - min-instances=0: escala a cero sin tráfico (hace que document.visibilityState ahorre dinero real)
# - cpu-throttling: CPU asignada solo durante peticiones activas (cero costo ocioso 24/7)
# =============================================================================

$ErrorActionPreference = "Stop"

$ServiceName = if ($env:SERVICE_NAME) { $env:SERVICE_NAME } else { "wellq-admin-backend" }
$Region = if ($env:REGION) { $env:REGION } else { "us-central1" }
$ProjectId = if ($env:PROJECT_ID) { $env:PROJECT_ID } else { (gcloud config get-value project) }

Write-Host "==> Desplegando $ServiceName en Google Cloud Run (Region: $Region, Proyecto: $ProjectId)..."

gcloud run deploy $ServiceName `
  --source . `
  --platform managed `
  --region $Region `
  --project $ProjectId `
  --allow-unauthenticated `
  --port 8080 `
  --max-instances 1 `
  --min-instances 0 `
  --cpu-throttling `
  --timeout 300

Write-Host "==> Despliegue finalizado con exito."
