import Swal from 'sweetalert2';
import { getAccessToken, clearTokens } from '../services/auth';
import { SSEDecoder } from './sseParser.js';

// ─────────────────────────────────────────────────────────────────────────────
// client.js — HTTP client centralizado para WellQ Admin
//
// FUENTE ÚNICA DE VERDAD: el token siempre se lee a través de getAccessToken()
// definido en services/auth.js. Nunca acceder a localStorage directamente aquí.
// ─────────────────────────────────────────────────────────────────────────────

export const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const formatApiDetail = (detail) => {
  if (typeof detail === 'string') return detail;

  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        const location = Array.isArray(item?.loc)
          ? item.loc.filter((part) => part !== 'body').join('.')
          : '';
        const message = item?.msg || item?.message || '';
        return [location, message].filter(Boolean).join(': ');
      })
      .filter(Boolean)
      .join('\n');
  }

  if (detail && typeof detail === 'object') {
    return detail.message || detail.error || JSON.stringify(detail);
  }

  return '';
};

// ─── Core fetch ───────────────────────────────────────────────────────────────

export const apiFetch = async (path, options = {}) => {
  const token = getAccessToken(); // ✅ lee 'wellq_access_token' desde auth.js

  const headers = {
    ...(options.body != null ? { 'Content-Type': 'application/json' } : {}),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(options.headers ?? {}),
  };

  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  });

  const text = await res.text();

  if (!res.ok) {
    let detail = '';
    try {
      const payload = text ? JSON.parse(text) : null;
      detail = formatApiDetail(payload?.detail);
    } catch {
      detail = text;
    }

    if (res.status === 403) {
      Swal.fire({
        icon:                'error',
        title:               'Acceso denegado',
        text:                detail || "Se requiere el rol 'super_admin' para esta operación.",
        confirmButtonColor:  '#0D9488',
        confirmButtonText:   'Entendido',
      });
      const err = new Error(detail || 'Forbidden');
      err.status = 403;
      throw err;
    }

    if (res.status === 401) {
      Swal.fire({
        icon:               'warning',
        title:              'Sesión expirada',
        text:               'Tu sesión ha expirado. Por favor, vuelve a iniciar sesión.',
        confirmButtonColor: '#0D9488',
      }).then(() => {
        clearTokens();                   // ✅ borra wellq_access_token, wellq_refresh_token y wellq_user
        window.location.href = '/login';
      });
      const err = new Error('Unauthorized');
      err.status = 401;
      throw err;
    }

    const err = new Error(detail || `HTTP ${res.status} — ${path}`);
    if (import.meta.env.DEV) console.warn('[apiFetch]', err.message);
    throw err;
  }

  return text ? JSON.parse(text) : null;
};

// ─── Support Tickets ──────────────────────────────────────────────────────────

export const fetchSupportTickets = (params = {}) => {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString();
  return apiFetch(`/api/support-tickets${qs ? `?${qs}` : ''}`);
};

export const fetchSupportTicket = (ticketId) =>
  apiFetch(`/api/support-tickets/${ticketId}`);

/**
 * Actualiza el ciclo de vida de un ticket.
 * @param {string} ticketId
 * @param {{ status?: string, responder_id?: string, responder_name?: string, solution?: string }} body
 */
export const patchSupportTicket = (ticketId, body) =>
  apiFetch(`/api/support-tickets/${ticketId}`, {
    method: 'PATCH',
    body:   JSON.stringify(body),
  });

/**
 * Elimina un ticket permanentemente.
 * @param {string} ticketId
 */
export const deleteSupportTicket = (ticketId) =>
  apiFetch(`/api/support-tickets/${ticketId}`, { method: 'DELETE' });

/**
 * Crea un ticket nuevo desde el backoffice.
 * @param {{ title: string, description: string, category: string, clinic_id?: string, reporter_name?: string, reporter_email?: string }} body
 */
export const createSupportTicket = (body) =>
  apiFetch('/api/support-tickets', {
    method: 'POST',
    body:   JSON.stringify(body),
  });

// ─── Support Config: Categorías ───────────────────────────────────────────────

export const fetchTicketCategories = () =>
  apiFetch('/api/support-tickets/categories');

export const createTicketCategory = (body) =>
  apiFetch('/api/support-tickets/categories', {
    method: 'POST',
    body:   JSON.stringify(body),
  });

export const updateTicketCategory = (categoryId, body) =>
  apiFetch(`/api/support-tickets/categories/${categoryId}`, {
    method: 'PATCH',
    body:   JSON.stringify(body),
  });

export const deleteTicketCategory = (categoryId) =>
  apiFetch(`/api/support-tickets/categories/${categoryId}`, { method: 'DELETE' });

// ─── Support Config: Resolutores ──────────────────────────────────────────────

export const fetchSupportResponders = () =>
  apiFetch('/api/support-tickets/responders');

// Alias de compatibilidad
export const fetchResponders = fetchSupportResponders;

export const createResponder = (body) =>
  apiFetch('/api/support-tickets/responders', {
    method: 'POST',
    body:   JSON.stringify(body),
  });

export const updateResponder = (responderId, body) =>
  apiFetch(`/api/support-tickets/responders/${responderId}`, {
    method: 'PATCH',
    body:   JSON.stringify(body),
  });

export const deleteResponder = (responderId) =>
  apiFetch(`/api/support-tickets/responders/${responderId}`, { method: 'DELETE' });

// ─── Otros endpoints ──────────────────────────────────────────────────────────

export const fetchPatientHealth = (clinicId) =>
  apiFetch(`/api/clinics/${clinicId}/patient-health`);

export const fetchSyncStatus = () =>
  apiFetch('/api/v1/sync-status');

// ─── Sincronización de datos (Recomendación #7 — v1) ──────────────────────────

export const fetchSyncConfig = () =>
  apiFetch('/api/v1/sync/config');

/** @param {6|12|24} frequencyHours */
export const updateSyncFrequency = (frequencyHours) =>
  apiFetch('/api/v1/sync/config', {
    method: 'PUT',
    body: JSON.stringify({ frequency_hours: frequencyHours }),
  });

export const triggerSyncRun = (body = {}) =>
  apiFetch('/api/v1/sync/run', { method: 'POST', body: JSON.stringify(body) });

export const fetchSyncHistory = (params = {}) => {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString();
  return apiFetch(`/api/v1/sync/history${qs ? `?${qs}` : ''}`);
};

// ─── Clínicas ─────────────────────────────────────────────────────────────────

export const fetchClinics = (params = {}) => {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString();
  return apiFetch(`/api/clinics${qs ? `?${qs}` : ''}`);
};

export const fetchClinic             = (clinicId)       => apiFetch(`/api/clinics/${clinicId}`);
export const createClinic            = (body)           => apiFetch('/api/clinics', { method: 'POST',  body: JSON.stringify(body) });
export const updateClinic            = (clinicId, body) => apiFetch(`/api/clinics/${clinicId}`, { method: 'PATCH', body: JSON.stringify(body) });
/** Requiere super_admin — 403 se muestra automáticamente con Swal */
export const deleteClinic            = (clinicId)       => apiFetch(`/api/clinics/${clinicId}`, { method: 'DELETE' });
export const fetchClinicContact      = (clinicId)       => apiFetch(`/api/clinics/${clinicId}/contact`);
export const fetchClinicSubscription = (clinicId)       => apiFetch(`/api/clinics/${clinicId}/subscription`);
export const fetchClinicUsage        = (clinicId)       => apiFetch(`/api/clinics/${clinicId}/usage`);
export const fetchClinicLicense      = (clinicId)       => apiFetch(`/api/clinics/${clinicId}/license`);
export const fetchClinicInvoices     = (clinicId)       => apiFetch(`/api/clinics/${clinicId}/invoices`);
/** Requiere super_admin — 403 se muestra automáticamente con Swal */
export const impersonateClinic       = (clinicId, body) => apiFetch(`/api/clinics/${clinicId}/impersonate`, { method: 'POST', body: JSON.stringify(body) });

// ─── Planes ───────────────────────────────────────────────────────────────────

export const fetchPlans = (params = {}) => {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString();
  return apiFetch(`/api/plans${qs ? `?${qs}` : ''}`);
};

export const fetchPlan     = (planId)       => apiFetch(`/api/plans/${planId}`);
/** Requiere super_admin — 403 se muestra automáticamente con Swal */
export const createPlan    = (body)         => apiFetch('/api/plans', { method: 'POST', body: JSON.stringify(body) });
export const updatePlan    = (planId, body) => apiFetch(`/api/plans/${planId}`, { method: 'PUT',  body: JSON.stringify(body) });
export const duplicatePlan = (planId, body) => apiFetch(`/api/plans/${planId}/duplicate`, { method: 'POST', body: JSON.stringify(body) });
export const archivePlan   = (planId)       => apiFetch(`/api/plans/${planId}/archive`, { method: 'POST' });
export const restorePlan   = (planId)       => apiFetch(`/api/plans/${planId}/restore`, { method: 'POST' });
/** Requiere super_admin — 403 se muestra automáticamente con Swal */
export const deletePlan    = (planId)       => apiFetch(`/api/plans/${planId}`, { method: 'DELETE' });

// ─── Notificaciones de incidentes (Mejora #19) ────────────────────────────────

export const fetchIncidentNotificationConfig = () =>
  apiFetch('/api/v1/incident-notifications/config');

export const fetchIncidentDeliveries = (limit = 20) =>
  apiFetch(`/api/v1/incident-notifications/deliveries?limit=${limit}`);

/** @param {'email'|'slack'} channel */
export const sendIncidentTest = (channel) =>
  apiFetch('/api/v1/incident-notifications/test', {
    method: 'POST',
    body: JSON.stringify({ channel }),
  });

export const dispatchAlertNotification = (alertId, channels) =>
  apiFetch(`/api/v1/incident-notifications/alerts/${encodeURIComponent(alertId)}/dispatch`, {
    method: 'POST',
    body: JSON.stringify(channels ? { channels } : {}),
  });

// ─── Telemetría y registros reales (Rec #5) ─────────────────────────────────

/**
 * Métricas agregadas de la API (P50/P90/P99, tasa de error, RPM, serie).
 * @param {{ window?: '1h'|'24h'|'7d', endpoint?: string, clinic_id?: string }} params
 */
export const fetchTelemetryMetrics = (params = {}) => {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString();
  return apiFetch(`/api/platform/telemetry/metrics${qs ? `?${qs}` : ''}`);
};

/**
 * Eventos recientes de análisis de postura.
 * @param {{ window?: '1h'|'24h'|'7d', clinic_id?: string, limit?: number }} params
 */
export const fetchTelemetryLogs = (params = {}) => {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString();
  return apiFetch(`/api/platform/telemetry/logs${qs ? `?${qs}` : ''}`);
};

/**
 * Abre el stream SSE de telemetría mediante fetch() y ReadableStream.
 *
 * SEGURIDAD (OWASP): el JWT viaja en el header estándar Authorization: Bearer,
 * NUNCA en la URL ni query params (?token=).
 *
 * Maneja reconexión controlada con backoff exponencial y jitter (1s..30s).
 * Detiene la reconexión inmediatamente si el servidor responde 401 o 403.
 *
 * @param {{ clinicId?: string, endpoint?: string, onLog?: Function,
 *           onMetrics?: Function, onOpen?: Function, onError?: Function }} handlers
 * @returns {() => void} close() — cierra el stream y cancela reintentos.
 */
export const streamTelemetryEvents = ({ clinicId, endpoint, onLog, onMetrics, onOpen, onError } = {}) => {
  const qs = new URLSearchParams();
  if (clinicId) qs.set('clinic_id', clinicId);
  if (endpoint) qs.set('endpoint', endpoint);

  let controller = new AbortController();
  let stopped = false;
  let isConnecting = false;
  let retryCount = 0;
  let retryTimer = null;

  const connect = async () => {
    if (stopped || isConnecting) return;
    isConnecting = true;
    controller = new AbortController();
    const token = getAccessToken();

    try {
      const res = await fetch(`${API_BASE}/api/platform/telemetry/stream${qs.toString() ? `?${qs.toString()}` : ''}`, {
        method: 'GET',
        headers: {
          Accept: 'text/event-stream',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        signal: controller.signal,
      });

      if (!res.ok) {
        let detail = '';
        try {
          detail = formatApiDetail((await res.json())?.detail);
        } catch {
          detail = '';
        }
        const err = new Error(detail || `HTTP ${res.status}`);
        err.status = res.status;
        isConnecting = false;

        // Si es 401 o 403: NO hacer loops de reintento con tokens vencidos o sin permisos
        if (res.status === 401 || res.status === 403) {
          stopped = true;
          if (retryTimer) {
            clearTimeout(retryTimer);
            retryTimer = null;
          }
          onError?.(err);
          return;
        }
        throw err;
      }

      isConnecting = false;
      retryCount = 0;
      onOpen?.();

      const reader = res.body.getReader();
      const sseDecoder = new SSEDecoder();

      for (;;) {
        const { value, done } = await reader.read();
        const events = sseDecoder.push(value, done);

        for (const ev of events) {
          if (ev.event === 'log') {
            onLog?.(ev.data);
          } else if (ev.event === 'metrics') {
            onMetrics?.(ev.data);
          }
        }

        if (done) break;
      }

      // Si el stream se cierra normalmente (ej. timeout de Cloud Run), reconectar
      if (!stopped) {
        scheduleReconnect();
      }
    } catch (err) {
      isConnecting = false;
      if (err.name === 'AbortError' || stopped) return;
      onError?.(err);
      scheduleReconnect();
    }
  };

  const scheduleReconnect = () => {
    if (stopped) return;
    // Backoff exponencial con jitter: 1s, 2s, 4s, 8s, máx 30s
    const baseDelay = Math.min(30000, 1000 * Math.pow(2, retryCount));
    const jitter = Math.random() * 500;
    const delay = baseDelay + jitter;
    retryCount++;
    if (retryTimer) clearTimeout(retryTimer);
    retryTimer = setTimeout(connect, delay);
  };

  connect();

  return () => {
    stopped = true;
    if (retryTimer) {
      clearTimeout(retryTimer);
      retryTimer = null;
    }
    controller.abort();
  };
};

// ─── Asistente conversacional (Mejora #20) ────────────────────────────────────

export const fetchAssistantStatus = () => apiFetch('/api/v1/assistant/status');

/**
 * Envía una pregunta al asistente y entrega los eventos SSE a medida que llegan.
 * Eventos: meta · tool · delta · done · error (ver backend/app/routers/assistant.py).
 *
 * @param {string} message
 * @param {{role:'user'|'assistant', text:string}[]} history
 * @param {(event:object)=>void} onEvent
 * @param {AbortSignal} [signal]
 */
export const streamAssistantChat = async (message, history, onEvent, signal) => {
  const token = getAccessToken();
  const res = await fetch(`${API_BASE}/api/v1/assistant/chat`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ message, history }),
    signal,
  });

  if (!res.ok) {
    let detail = '';
    try {
      detail = formatApiDetail((await res.json())?.detail);
    } catch {
      detail = '';
    }
    const err = new Error(detail || `HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep;
    while ((sep = buffer.indexOf('\n\n')) !== -1) {
      const block = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const dataLine = block.split('\n').find((line) => line.startsWith('data:'));
      if (!dataLine) continue;
      try {
        onEvent(JSON.parse(dataLine.slice(5).trim()));
      } catch {
        /* bloque incompleto o inválido: se ignora */
      }
    }
  }
};
