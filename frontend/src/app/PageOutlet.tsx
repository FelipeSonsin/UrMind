import { lazy } from 'react';
import { CitizenPage } from '../pages/citizen/CitizenPage';
import { useAppState } from './AppState';
import { useSlowResponse } from './hooks/useSlowResponse';

// A área da equipe só existe em /admin/: o site do cidadão não baixa o código dela.
const TeamArea = lazy(() =>
  import('../pages/team/TeamArea').then((m) => ({ default: m.TeamArea })),
);

/**
 * Espera longa por uma resposta: diz que é normal e quanto pode durar. A região de aviso
 * fica sempre na página (vazia), para o leitor de tela anunciar quando o texto entra; o
 * aviso em si flutua no pé do conteúdo, sem empurrar nada.
 */
function SlowNotice() {
  const slow = useSlowResponse();
  return (
    <div role="status">
      {slow && (
        <p className="loading-line slow-notice">
          Aguarde um pouco: depois de um tempo sem uso, o UrMind pode levar até um minuto para
          responder.
        </p>
      )}
    </div>
  );
}

/**
 * Conteúdo da página atual: avisos gerais (no topo: atualização do app, aviso da última
 * ação, erro de rascunho; flutuando no pé: espera longa pela resposta) e a página da rota. O site do cidadão e a área da equipe
 * escolhem as próprias páginas em `CitizenPage` e `TeamArea`.
 */
export function PageOutlet() {
  const { privatePage, notice, drafts, serviceWorker } = useAppState();
  return (
    <>
      <SlowNotice />
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
