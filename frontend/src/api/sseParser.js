/**
 * sseParser.js — Decodificador y parseador defensivo de Server-Sent Events (SSE)
 *
 * Características de robustez:
 * 1. Decodificación UTF-8 segura con TextDecoder stream: true + flush final.
 * 2. Buffer para fragmentación TCP/SSE (soporta cortes arbitrarios por \n\n o \r\n\r\n).
 * 3. Ignora líneas de comentario (que inician con ':') como heartbeats (: ping).
 * 4. Concatenación de múltiples líneas 'data:' según la especificación W3C / WHATWG.
 * 5. Parseo defensivo de JSON con try/catch sin romper el stream ante frames inválidos.
 */

export class SSEDecoder {
  constructor() {
    this.decoder = new TextDecoder('utf-8');
    this.buffer = '';
  }

  /**
   * Recibe un chunk Uint8Array (o string) y extrae los eventos completos parseados.
   * @param {Uint8Array|string} [chunk]
   * @param {boolean} [isDone=false]
   * @returns {Array<{ event: string, data: any, rawData: string }>}
   */
  push(chunk, isDone = false) {
    if (chunk) {
      if (typeof chunk === 'string') {
        this.buffer += chunk;
      } else {
        this.buffer += this.decoder.decode(chunk, { stream: !isDone });
      }
    }

    if (isDone) {
      this.buffer += this.decoder.decode(); // flush final de bytes residuales
    }

    const events = [];

    for (;;) {
      const matchRNRN = this.buffer.indexOf('\r\n\r\n');
      const matchNN = this.buffer.indexOf('\n\n');
      let sepIdx = -1;
      let sepLen = 0;

      if (matchRNRN !== -1 && (matchNN === -1 || matchRNRN < matchNN)) {
        sepIdx = matchRNRN;
        sepLen = 4;
      } else if (matchNN !== -1) {
        sepIdx = matchNN;
        sepLen = 2;
      } else {
        break; // No hay frame delimitado completo aún: se conserva en buffer
      }

      const frame = this.buffer.slice(0, sepIdx);
      this.buffer = this.buffer.slice(sepIdx + sepLen);

      const parsed = parseSSEFrame(frame);
      if (parsed) {
        events.push(parsed);
      }
    }

    return events;
  }
}

/**
 * Parsea un frame de texto SSE individual delimitado.
 * @param {string} frame
 * @returns {{ event: string, data: any, rawData: string } | null}
 */
export function parseSSEFrame(frame) {
  if (!frame || !frame.trim()) return null;

  const lines = frame.split(/\r?\n/);
  let eventType = 'message';
  const dataLines = [];

  for (const line of lines) {
    // Ignorar líneas de comentario que comiencen con ':'
    if (line.startsWith(':')) {
      continue;
    }
    if (line.startsWith('event:')) {
      eventType = line.slice(6).trim();
    } else if (line.startsWith('data:')) {
      let content = line.slice(5);
      if (content.startsWith(' ')) {
        content = content.slice(1);
      }
      dataLines.push(content);
    }
  }

  if (dataLines.length === 0) {
    return null;
  }

  // Concatenación según spec SSE: múltiples líneas 'data:' unidas con \n
  const rawData = dataLines.join('\n');
  let data = rawData;
  try {
    data = JSON.parse(rawData);
  } catch {
    // Si no es JSON válido, data conserva el string crudo
  }

  return { event: eventType, data, rawData };
}
