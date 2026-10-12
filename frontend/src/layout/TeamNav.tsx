import { Ellipsis, Globe, LogOut } from 'lucide-react';
import { useAppState } from '../app/AppState';
import { internalOrder, internalShort, privateNavigation } from '../app/routes';
import { auth } from '../services/auth';

/** Menu da equipe: estável na lateral; no celular, Painel, Fila e Mapa embaixo e "Mais". */
export function TeamNav() {
  const { page, team, reports } = useAppState();
  // Páginas que moram em "Mais" no celular: a aba fica marcada quando uma delas está aberta.
  const inMore =
    page === 'private-detail' || internalOrder.some((id) => id === page && !internalShort[id]);
  const items = internalOrder
    .filter((id) => id !== 'admin' || team.canAdmin)
    .map((id) => privateNavigation.find((item) => item.id === id)!);
  return (
    <nav className="main-nav team-nav" aria-label="Navegação interna">
      {items.map((item) => (
        <a
          key={item.id}
          className={`nav-item${internalShort[item.id] ? ' is-primary' : ' nav-tool'}`}
          href={item.href}
          // A aba "Fila" sempre abre a fila de trabalho (sem filtro vindo do Painel).
          onClick={item.id === 'review' ? reports.resetQueue : undefined}
          aria-current={
            page === item.id || (page === 'private-detail' && item.id === 'private-events')
              ? 'page'
              : undefined
          }
        >
          <item.icon size={18} strokeWidth={1.8} aria-hidden="true" />
          <span className="label-long">{item.label}</span>
          {internalShort[item.id] && <span className="label-short">{internalShort[item.id]}</span>}
        </a>
      ))}
      <details className="nav-more">
        <summary aria-current={inMore ? 'page' : undefined}>
          <Ellipsis size={18} strokeWidth={1.8} aria-hidden="true" />
          Mais
        </summary>
        <div className="nav-more-menu">
          {items
            .filter((item) => !internalShort[item.id])
            .map((item) => (
              <a key={item.id} href={item.href}>
                <item.icon size={18} strokeWidth={1.8} aria-hidden="true" /> {item.label}
              </a>
            ))}
          <a href="/">
            <Globe size={18} strokeWidth={1.8} aria-hidden="true" /> Site do cidadão
          </a>
          <button type="button" className="ghost" onClick={() => void auth.signOut()}>
            <LogOut size={18} strokeWidth={1.8} aria-hidden="true" /> Sair
          </button>
        </div>
      </details>
    </nav>
  );
}
