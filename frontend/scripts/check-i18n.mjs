// Mejora #24 — Verifica que en.json y es.json tengan las mismas claves
// y que toda clave usada con t('...') o tx('...') exista en ambos idiomas.
// Uso: npm run check:i18n
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

const root = new URL('../src/', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const en = JSON.parse(readFileSync(join(root, 'locales/en.json'), 'utf8'));
const es = JSON.parse(readFileSync(join(root, 'locales/es.json'), 'utf8'));

const flat = (obj, pre = '') => Object.entries(obj).flatMap(([k, v]) =>
  v && typeof v === 'object' ? flat(v, `${pre}${k}.`) : [`${pre}${k}`]);
const enKeys = new Set(flat(en));
const esKeys = new Set(flat(es));

const errors = [];
for (const k of enKeys) if (!esKeys.has(k)) errors.push(`Falta en es.json: ${k}`);
for (const k of esKeys) if (!enKeys.has(k)) errors.push(`Falta en en.json: ${k}`);

const walk = (dir) => readdirSync(dir).flatMap((f) => {
  const p = join(dir, f);
  return statSync(p).isDirectory() ? walk(p) : /\.(jsx?|tsx?)$/.test(f) ? [p] : [];
});
const re = /\b(?:t|tx)\(\s*['"]([A-Za-z0-9_]+\.[A-Za-z0-9_.]+)['"]/g;
let used = 0;
for (const file of walk(root)) {
  const src = readFileSync(file, 'utf8');
  for (const m of src.matchAll(re)) {
    used++;
    const key = m[1];
    if (!enKeys.has(key) && !esKeys.has(key)) errors.push(`Clave inexistente "${key}" en ${file.replace(root, 'src/')}`);
  }
}

if (errors.length) {
  console.error(`✗ ${errors.length} problema(s) de traducción:`);
  errors.forEach((e) => console.error('  - ' + e));
  process.exit(1);
}
console.log(`✓ Traducciones OK: ${enKeys.size} claves por idioma, ${used} usos revisados.`);
