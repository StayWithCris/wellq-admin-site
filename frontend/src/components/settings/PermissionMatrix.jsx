import React from 'react';
import { Check, Minus } from 'lucide-react';

// Rec #17 — Matriz visual de permisos (roles × permisos, agrupados por módulo). Solo lectura.
export default function PermissionMatrix({ roles = [], permissions = [] }) {
  if (!roles.length || !permissions.length) {
    return <p className="text-sm text-wellq-gray py-8 text-center">No hay roles o permisos para mostrar.</p>;
  }

  const byModule = permissions.reduce((acc, p) => {
    const key = p.module || 'otros';
    (acc[key] = acc[key] || []).push(p);
    return acc;
  }, {});

  const has = (role, key) => role.name === 'super_admin' || (role.permissions ?? []).includes(key);

  return (
    <div className="overflow-x-auto rounded-2xl border border-wellq-gray/20 dark:border-[#1e293b]">
      <table className="min-w-full text-sm">
        <thead>
          <tr className="bg-wellq-gray/5 dark:bg-white/[0.03]">
            <th className="text-left px-4 py-3 font-black text-wellq-dark dark:text-white sticky left-0 bg-inherit">Permiso</th>
            {roles.map((r) => (
              <th key={r.id} className="px-4 py-3 text-center font-bold text-wellq-dark dark:text-white whitespace-nowrap">
                {r.name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {Object.entries(byModule).map(([module, perms]) => (
            <React.Fragment key={module}>
              <tr>
                <td colSpan={roles.length + 1} className="px-4 py-2 text-[11px] font-black uppercase tracking-wider text-wellq-cyan bg-wellq-cyan/5">
                  {module}
                </td>
              </tr>
              {perms.map((p) => (
                <tr key={p.key} className="border-t border-wellq-gray/10 dark:border-white/5">
                  <td className="px-4 py-2 sticky left-0 bg-white dark:bg-[#0b1017]">
                    <div className="font-semibold text-wellq-dark dark:text-white">{p.label || p.key}</div>
                    <div className="text-[11px] text-wellq-gray">{p.key}</div>
                  </td>
                  {roles.map((r) => (
                    <td key={r.id} className="px-4 py-2 text-center">
                      {has(r, p.key)
                        ? <Check size={16} className="inline text-green-500" aria-label="Permitido" />
                        : <Minus size={16} className="inline text-wellq-gray/40" aria-label="No permitido" />}
                    </td>
                  ))}
                </tr>
              ))}
            </React.Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}
