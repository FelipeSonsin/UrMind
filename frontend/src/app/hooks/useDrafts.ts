import { useCallback, useEffect, useState } from 'react';
import { drafts, type CaptureDraft } from '../../services/drafts';

/**
 * Rascunhos guardados neste navegador (IndexedDB): fotos que ainda não chegaram ao
 * UrMind, por falta de sessão ou de rede. `editing` é o rascunho aberto no registro.
 */
export function useDrafts(setNotice: (text: string) => void) {
  const [localDrafts, setLocalDrafts] = useState<CaptureDraft[]>([]);
  const [draftError, setDraftError] = useState('');
  const [draftsLoaded, setDraftsLoaded] = useState(false);
  const [editing, setEditing] = useState<CaptureDraft>();
  const reloadDrafts = useCallback(async () => {
    try {
      setLocalDrafts(await drafts.list());
      setDraftError('');
      setDraftsLoaded(true);
    } catch {
      setDraftsLoaded(false);
      setDraftError(
        'Não foi possível abrir os rascunhos deste aparelho. Verifique se o navegador permite guardar dados e se há espaço livre.',
      );
    }
  }, []);
  useEffect(() => {
    void reloadDrafts();
  }, [reloadDrafts]);
  /** Exclui de vez: quem chama já pediu a confirmação na própria página (ConfirmButton). */
  async function remove(draft: CaptureDraft) {
    try {
      await drafts.remove(draft.id);
      await reloadDrafts();
      setNotice('Rascunho e foto excluídos deste aparelho.');
    } catch {
      setDraftError('Não foi possível excluir o rascunho. Tente novamente.');
    }
  }
  return {
    list: localDrafts,
    loaded: draftsLoaded,
    error: draftError,
    setError: setDraftError,
    reload: reloadDrafts,
    editing,
    setEditing,
    remove,
  };
}
export type DraftsState = ReturnType<typeof useDrafts>;
