/**
 * Duas superfícies no mesmo build, em endereços separados que não se cruzam:
 * o site do cidadão (`/`) e a área da equipe (`/admin/`). Cada uma só abre as
 * próprias rotas e guarda a própria sessão de login no navegador.
 */
export type Surface = 'public' | 'admin';

/** Endereço da área da equipe (o build gera `admin/index.html`). */
export const ADMIN_PATH = '/admin/';

export function currentSurface(
  // Fora do navegador (testes de unidade em Node) não há `location`: vale o site público.
  pathname: string = typeof location === 'undefined' ? '/' : location.pathname,
): Surface {
  return /^\/admin(?:\/|$)/.test(pathname) ? 'admin' : 'public';
}
