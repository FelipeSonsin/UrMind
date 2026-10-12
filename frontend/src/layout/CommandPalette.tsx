import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { CornerDownLeft, Search } from 'lucide-react';
import { useAppState } from '../app/AppState';
import { buildCommands, filterCommands, type Command } from './commands';

/** Atalho da paleta: Ctrl+K (Windows, Linux) ou ⌘K (Mac). */
export function isPaletteShortcut(event: globalThis.KeyboardEvent): boolean {
  return (event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === 'k';
}

function go(command: Command) {
  if (command.href.startsWith('#')) location.hash = command.href;
  else location.href = command.href;
}

/**
 * Paleta "Ir para…": busca páginas, relatos desta sessão e protocolos, e leva até eles
 * pelo teclado (setas e Enter) ou pelo toque. Diálogo nativo: Escape e o fundo fecham.
 * Estilos: styles/layout/palette.css.
 */
export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const app = useAppState();
  const dialog = useRef<HTMLDialogElement>(null);
  const input = useRef<HTMLInputElement>(null);
  // Quem abriu a paleta recebe o foco de volta quando ela fecha.
  const opener = useRef<HTMLElement | null>(null);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const listId = useId();
  const results = useMemo(() => filterCommands(buildCommands(app, query), query), [app, query]);
  const current = results[Math.min(active, results.length - 1)];

  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (open && !element.open) {
      opener.current =
        document.activeElement instanceof HTMLElement ? document.activeElement : null;
      setQuery('');
      setActive(0);
      element.showModal();
      input.current?.focus();
    } else if (!open && element.open) {
      element.close();
    }
  }, [open]);

  function choose(command: Command) {
    onClose();
    go(command);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      const step = event.key === 'ArrowDown' ? 1 : -1;
      setActive((value) => (value + step + results.length) % Math.max(results.length, 1));
    } else if (event.key === 'Enter' && current) {
      event.preventDefault();
      choose(current);
    }
  }

  useEffect(() => {
    document.getElementById(`${listId}-${current?.id}`)?.scrollIntoView({ block: 'nearest' });
  }, [current?.id, listId]);

  const groups = [...new Set(results.map((command) => command.group))];
  return (
    <dialog
      ref={dialog}
      className="palette"
      aria-label="Ir para"
      onClose={() => {
        onClose();
        opener.current?.focus();
      }}
      onClick={(event) => {
        // Toque no fundo escurecido fecha; dentro da caixa, não.
        if (event.target === dialog.current) onClose();
      }}
    >
      {/* Fechada, a paleta não tem conteúdo: nada dela aparece em buscas na página. */}
      {open && (
        <div className="palette-box">
          <div className="palette-search">
            <Search size={18} strokeWidth={1.8} aria-hidden="true" />
            <input
              ref={input}
              type="text"
              role="combobox"
              aria-expanded={results.length > 0}
              aria-controls={results.length ? listId : undefined}
              aria-autocomplete="list"
              aria-activedescendant={current ? `${listId}-${current.id}` : undefined}
              aria-label="Buscar página, relato ou protocolo"
              placeholder="Buscar página, relato ou protocolo…"
              autoComplete="off"
              spellCheck={false}
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setActive(0);
              }}
              onKeyDown={onKeyDown}
            />
            <kbd className="palette-esc">Esc</kbd>
          </div>
          {/* A lista de opções só existe com resultado; o aviso de "nada encontrado" fica fora dela. */}
          {results.length > 0 ? (
            <div className="palette-list" id={listId} role="listbox" aria-label="Resultados">
              {groups.map((group) => (
                <ul key={group} role="group" aria-label={group}>
                  <li role="presentation" className="palette-group" aria-hidden="true">
                    {group}
                  </li>
                  {results
                    .filter((command) => command.group === group)
                    .map((command) => (
                      <li
                        key={command.id}
                        id={`${listId}-${command.id}`}
                        role="option"
                        aria-selected={command === current}
                        className="palette-option"
                        onPointerMove={() => setActive(results.indexOf(command))}
                        onClick={() => choose(command)}
                      >
                        <span className="palette-label">{command.label}</span>
                        {command.hint && <span className="palette-hint">{command.hint}</span>}
                        {command === current && (
                          <CornerDownLeft size={15} className="palette-enter" aria-hidden="true" />
                        )}
                      </li>
                    ))}
                </ul>
              ))}
            </div>
          ) : (
            <p className="palette-empty" role="status">
              Nada encontrado para “{query.trim()}”.
            </p>
          )}
          <p className="palette-foot" aria-hidden="true">
            <span>
              <kbd>↑</kbd> <kbd>↓</kbd> navegar
            </span>
            <span>
              <kbd>Enter</kbd> abrir
            </span>
          </p>
        </div>
      )}
    </dialog>
  );
}
