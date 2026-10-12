import { lazy } from 'react';
import { CitizenPage } from '../pages/citizen/CitizenPage';
import { useAppState } from './AppState';

// A área da equipe só existe em /admin/: o site do cidadão não baixa o código dela.
const TeamArea = lazy(() =>
  import('../pages/team/TeamArea').then((m) => ({ default: m.TeamArea })),
);

/**
 * Conteúdo da página atual: avisos gerais no topo (atualização do app, aviso da última
 * ação, erro de rascunho) e a página da rota. O site do cidadão e a área da equipe
 * escolhem as próprias páginas em `CitizenPage` e `TeamArea`.
 */
export function PageOutlet() {
  const { privatePage, notice, drafts, serviceWorker } = useAppState();
  return (
    <>
      {serviceWorker.needRefresh && (
        <div className="notice">
          Uma atualização está disponível. Salve seu rascunho antes de atualizar.{' '}
          <button className="secondary compact" onClick={serviceWorker.update}>
            Atualizar aplicativo
          </button>
        </div>
      )}
      {notice && (
        <p className="success" role="status">
          {notice}
        </p>
      )}
      {drafts.error && (
        <p className="error" role="alert">
          {drafts.error}
        </p>
      )}
      {privatePage ? <TeamArea /> : <CitizenPage />}
    </>
  );
}
