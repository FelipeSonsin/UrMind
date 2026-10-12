import { lazy } from 'react';
import { Camera, FileImage } from 'lucide-react';
import { useAppState } from '../../app/AppState';
import { ConfirmButton } from '../../components/ConfirmButton';
import { Photo } from '../../components/Photo';
import { EmptyState, PageHeader } from '../../components/ui';

const SignIn = lazy(() => import('../../components/SignIn').then((m) => ({ default: m.SignIn })));

/** Rascunhos neste aparelho: continuar a edição, enviar de novo ou excluir. */
export function DraftsPage() {
  const { session, drafts } = useAppState();
  return (
    <>
      {/* Um só botão de registrar: no topo quando há rascunhos; senão, no aviso abaixo. */}
      <PageHeader
        title="Rascunhos neste aparelho"
        description={
          drafts.list.length
            ? 'Fotos que ainda não foram enviadas. Elas ficam só neste aparelho: limpar os dados do navegador apaga as fotos.'
            : undefined
        }
        actions={
          drafts.list.length > 0 && (
            <a className="button" href="#/registrar" onClick={() => drafts.setEditing(undefined)}>
              <Camera size={17} aria-hidden="true" /> Registrar relato
            </a>
          )
        }
      />
      {!drafts.list.length ? (
        <section className="panel">
          <EmptyState
            icon={<FileImage size={22} strokeWidth={1.8} />}
            title={
              drafts.loaded
                ? 'Nenhum rascunho guardado'
                : drafts.error
                  ? 'Não foi possível abrir os rascunhos'
                  : 'Procurando rascunhos…'
            }
            action={
              drafts.loaded ? (
                <a
                  className="button"
                  href="#/registrar"
                  onClick={() => drafts.setEditing(undefined)}
                >
                  <Camera size={17} aria-hidden="true" /> Registrar relato
                </a>
              ) : (
                drafts.error && (
                  <button type="button" className="secondary" onClick={() => void drafts.reload()}>
                    Tentar de novo
                  </button>
                )
              )
            }
          >
            {drafts.loaded
              ? 'Se um envio não puder ser concluído, a foto fica aqui para você enviar depois.'
              : drafts.error
                ? 'Verifique se o navegador permite guardar dados e tente de novo.'
                : 'Os rascunhos aparecem aqui em instantes.'}
          </EmptyState>
        </section>
      ) : (
        <div className="draft-grid">
          {drafts.list.map((draft) => (
            <article className="panel draft-card" key={draft.id}>
              <Photo
                blob={draft.photo}
                alt={`Foto do rascunho de ${formatDate(draft.created_at)}`}
              />
              <div>
                {/* O que a pessoa escreveu identifica melhor que o nome do arquivo. */}
                <h2>{draft.note.trim() || 'Relato sem descrição'}</h2>
                <p className="draft-meta">
                  <time dateTime={draft.created_at}>{formatDate(draft.created_at)}</time>
                  <span className={`badge ${draft.coordinate ? 'tone-success' : 'tone-attention'}`}>
                    {draft.coordinate ? 'Local marcado' : 'Falta o local'}
                  </span>
                </p>
                <div className="actions">
                  <a
                    className="button secondary"
                    href="#/registrar"
                    onClick={() => drafts.setEditing(draft)}
                  >
                    Continuar edição
                  </a>
                  {/* Sem o aviso de privacidade aceito, o envio passa pelo formulário. */}
                  {draft.privacy_version ? (
                    <button
                      type="button"
                      disabled={drafts.sending === draft.id}
                      onClick={() => void drafts.send(draft)}
                    >
                      {drafts.sending === draft.id ? 'Enviando…' : 'Enviar'}
                    </button>
                  ) : (
                    <a
                      className="button"
                      href="#/registrar"
                      onClick={() => drafts.setEditing(draft)}
                    >
                      Revisar e enviar
                    </a>
                  )}
                  <ConfirmButton
                    className="text-button danger"
                    label="Excluir"
                    question="A foto e o texto deste rascunho saem deste aparelho e não podem ser recuperados."
                    confirmLabel="Excluir rascunho"
                    onConfirm={() => void drafts.remove(draft)}
                  />
                </div>
              </div>
            </article>
          ))}
        </div>
      )}
      {/* Para enviar não é preciso conta; a entrada fica recolhida para a equipe. */}
      {!session && (
        <details className="panel sign-in-more">
          <summary>Tem conta da equipe? Entrar</summary>
          <SignIn />
        </details>
      )}
    </>
  );
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' });
}
