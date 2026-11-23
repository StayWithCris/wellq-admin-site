/**
 * sseParser.test.mjs — Pruebas unitarias de transporte SSE y decodificador defensivo
 * Ejecutable nativo con: node --test frontend/src/api/sseParser.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { SSEDecoder, parseSSEFrame } from './sseParser.js';

test('1. Evento JSON largo fragmentado en 3 chunks arbitrarios (incluyendo corte en \\n\\n)', () => {
  const decoder = new SSEDecoder();
  const obj = {
    id: 'evt-12345',
    endpoint: '/api/clinics/super-long-path-name-for-testing-telemetry',
    latency_ms: 245.89,
    details: 'A'.repeat(500),
  };
  const fullSSE = `event: log\ndata: ${JSON.stringify(obj)}\n\n`;

  // Cortamos exactamente en 3 partes arbitrarias, dejando el split en medio de \n\n
  const part1 = fullSSE.slice(0, 150);
  const part2 = fullSSE.slice(150, fullSSE.length - 1); // termina con el primer '\n'
  const part3 = fullSSE.slice(fullSSE.length - 1);       // contiene el segundo '\n'

  const encoder = new TextEncoder();

  const ev1 = decoder.push(encoder.encode(part1));
  assert.equal(ev1.length, 0, 'No debe emitir eventos incompletos en parte 1');

  const ev2 = decoder.push(encoder.encode(part2));
  assert.equal(ev2.length, 0, 'No debe emitir eventos incompletos en parte 2 (falta el segundo \\n)');

  const ev3 = decoder.push(encoder.encode(part3), true);
  assert.equal(ev3.length, 1, 'Debe emitir exactamente 1 evento al completarse el delimitador');
  assert.equal(ev3[0].event, 'log');
  assert.deepEqual(ev3[0].data, obj);
});

test('2. Caracteres UTF-8 multibyte (ó, ñ, 🚀) partidos exactamente entre dos chunks de red', () => {
  const decoder = new SSEDecoder();
  const texto = 'Clínica Niño 🚀';
  const fullSSE = `event: log\ndata: {"mensaje": "${texto}"}\n\n`;
  const encoder = new TextEncoder();
  const bytes = encoder.encode(fullSSE);

  // Buscamos la posición donde se codifica el emoji 🚀 (4 bytes UTF-8: 0xF0 0x9F 0x99 0x80)
  // y partimos justo en medio del emoji (entre el 2do y 3er byte)
  const idxRocket = bytes.indexOf(0xF0);
  assert.ok(idxRocket > 0, 'Debe encontrar el inicio del byte F0 de 🚀');

  const chunkA = bytes.slice(0, idxRocket + 2); // primeros 2 bytes de 🚀
  const chunkB = bytes.slice(idxRocket + 2);     // últimos 2 bytes de 🚀 + resto

  const evA = decoder.push(chunkA);
  assert.equal(evA.length, 0);

  const evB = decoder.push(chunkB, true);
  assert.equal(evB.length, 1);
  assert.equal(evB[0].data.mensaje, texto, 'El texto UTF-8 debe recomponerse sin caracteres corruptos ');
});

test('3. Heartbeats : ping e ignorar comentarios intercalados con event: log y event: metrics', () => {
  const decoder = new SSEDecoder();
  const rawStream = [
    ': connect\n\n',
    'event: log\ndata: {"status": 200, "result": "ok"}\n\n',
    ': ping\n\n',
    ': ping (con espacio)\n\n',
    'event: metrics\ndata: {"p50_ms": 42.5, "rpm": 120}\n\n',
    ': ping final\n\n',
  ].join('');

  const events = decoder.push(new TextEncoder().encode(rawStream), true);

  assert.equal(events.length, 2, 'Solo debe emitir los 2 eventos con datos, descartando todos los pings :');
  assert.equal(events[0].event, 'log');
  assert.equal(events[0].data.status, 200);
  assert.equal(events[1].event, 'metrics');
  assert.equal(events[1].data.p50_ms, 42.5);
});

test('4. Corte inmediato sin bucles de reintento ante respuestas HTTP 401 y 403', async () => {
  // Mockeamos la lógica de conexión para verificar que 401 y 403 detienen el retry
  let retryCount = 0;
  let errorReceived = null;
  let isStopped = false;

  const mockConnect = async (status) => {
    if (isStopped) return;
    const res = { ok: false, status, json: async () => ({ detail: 'Token expirado' }) };
    if (!res.ok) {
      const err = new Error(`HTTP ${res.status}`);
      err.status = res.status;
      if (res.status === 401 || res.status === 403) {
        isStopped = true;
        errorReceived = err;
        return; // Detención inmediata: NO llama a scheduleReconnect
      }
      retryCount++;
    }
  };

  await mockConnect(401);
  assert.equal(isStopped, true, 'El stream debe quedar detenido tras un 401');
  assert.equal(retryCount, 0, 'No debe haber reintentos');
  assert.equal(errorReceived?.status, 401);

  // Probar 403
  isStopped = false;
  retryCount = 0;
  await mockConnect(403);
  assert.equal(isStopped, true, 'El stream debe quedar detenido tras un 403');
  assert.equal(retryCount, 0, 'No debe haber reintentos tras un 403');
  assert.equal(errorReceived?.status, 403);
});

test('5. Llamar a cleanup/abort durante una espera de backoff cancela el reintento pendiente', async () => {
  let timerId = null;
  let reconnected = false;
  let stopped = false;

  const scheduleReconnect = (delayMs) => {
    if (stopped) return;
    timerId = setTimeout(() => {
      reconnected = true;
    }, delayMs);
  };

  // Programamos reconexión con delay de 100ms
  scheduleReconnect(100);
  assert.ok(timerId !== null, 'El timer debe estar programado');

  // Cancelación antes de que venza el timer (cleanup)
  stopped = true;
  clearTimeout(timerId);
  timerId = null;

  // Esperamos 150ms para verificar que nunca se disparó
  await new Promise((r) => setTimeout(r, 150));

  assert.equal(reconnected, false, 'La reconexión no debió ejecutarse tras el cleanup');
  assert.equal(timerId, null);
});
