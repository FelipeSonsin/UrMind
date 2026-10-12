import { Camera, Ellipsis, FileImage, Info, Webcam } from 'lucide-react';
import { useAppState } from '../app/AppState';
import { navigation, pageAvailable, publicPrimary, publicTools } from '../app/routes';

/**
 * Menu do cidadão. Computador: "Registrar relato" no topo, as tarefas e as
 * ferramentas. Celular: barra inferior com Registrar no centro e "Mais".
 */
/** Página interna acende a aba da sua seção (ocorrência → Mapa, relato → Meus relatos). */
const SECTION: Record<string, string> = {
  events: 'map',
  'event-detail': 'map',
  processing: 'my-reports',
};
const MORE_PAGES = ['robot-camera', 'robot-phone', 'drafts', 'about', 'analysis', 'settings'];

export function PublicNav() {
  const { page, route, setNotice, drafts } = useAppState();
  const hasDrafts = drafts.loaded && drafts.list.length > 0;
  // O resultado do próprio relato pertence a "Meus relatos", não ao Mapa.
  const section = page === 'event-detail' && route.owner ? 'my-reports' : (SECTION[page] ?? page);
  return (
    <nav className="main-nav" aria-label="Navegação principal">
      {pageAvailable('capture') && (
        <a
          className="nav-cta"
          href="#/registrar"
          aria-current={page === 'capture' ? 'page' : undefined}
          onClick={() => {
            setNotice('');
            drafts.setEditing(undefined);
          }}
        >
          <Camera size={18} strokeWidth={2} aria-hidden="true" />
          <span className="label-long">Registrar relato</span>
          <span className="label-short">Registrar</span>
        </a>
      )}
      {publicPrimary
        // Só entra no menu o que está ligado no registro de capacidades.
        .filter((id) => pageAvailable(id))
        .map((id) => navigation.find((item) => item.id === id)!)
        .map((item) => (
          <a
            key={item.id}
            className={`nav-item nav-${item.id}`}
            href={item.href}
            aria-current={section === item.id ? 'page' : undefined}
            onClick={() => setNotice('')}
          >
            <item.icon size={18} strokeWidth={1.8} aria-hidden="true" />
            {item.label}
          </a>
        ))}
      <span className="nav-group" aria-hidden="true">
        Ferramentas
      </span>
      {publicTools
        .filter((id) => pageAvailable(id))
        // Rascunhos só aparecem quando existem: fotos guardadas sem envio.
        .filter((id) => id !== 'drafts' || hasDrafts || page === 'drafts')
        .map((id) => navigation.find((item) => item.id === id)!)
        .map((item) => (
          <a
            key={item.id}
            className="nav-item nav-tool"
            href={item.href}
            aria-current={page === item.id ? 'page' : undefined}
            onClick={() => setNotice('')}
          >
            <item.icon size={18} strokeWidth={1.8} aria-hidden="true" />
            {item.label}
            {item.id === 'drafts' && drafts.list.length > 0 && (
              <span className="nav-count" aria-label={`${drafts.list.length} rascunhos`}>
                {drafts.list.length}
              </span>
            )}
          </a>
        ))}
      {/* Celular: ferramentas e páginas informativas num menu "Mais". */}
      <details className="nav-more">
        <summary aria-current={MORE_PAGES.includes(page) ? 'page' : undefined}>
          <Ellipsis size={18} strokeWidth={1.8} aria-hidden="true" />
          Mais
        </summary>
        <div className="nav-more-menu">
          {pageAvailable('robot-camera') && (
            <a href="#/camera-robo">
              <Webcam size={18} strokeWidth={1.8} aria-hidden="true" /> Câmera do robô
            </a>
          )}
          {hasDrafts && (
            <a href="#/drafts">
              <FileImage size={18} strokeWidth={1.8} aria-hidden="true" /> Rascunhos
              <span className="nav-count">{drafts.list.length}</span>
            </a>
          )}
          <a href="#/sobre">
            <Info size={18} strokeWidth={1.8} aria-hidden="true" /> Sobre e privacidade
          </a>
        </div>
      </details>
    </nav>
  );
}
