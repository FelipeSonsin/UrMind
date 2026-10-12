import { useRef, useState, type KeyboardEvent } from 'react';
import type { FlowStage } from '../content/systemFlow';

/**
 * Caminho de um relato, etapa por etapa. Cada etapa é uma aba: clique, toque ou setas
 * do teclado mostram o que acontece nela e quem decide. Só a etapa planejada leva
 * rótulo ("Planejado"), tracejada e nunca como se já funcionasse; as demais funcionam
 * hoje. Estilos: styles/components/system-flow.css.
 */
export function SystemFlow({ stages, label }: { stages: FlowStage[]; label: string }) {
  const [selected, setSelected] = useState(0);
  const tabs = useRef<Array<HTMLButtonElement | null>>([]);
  const current = stages[selected] ?? stages[0];

  function move(event: KeyboardEvent<HTMLButtonElement>) {
    const last = stages.length - 1;
    const next =
      event.key === 'ArrowRight' || event.key === 'ArrowDown'
        ? Math.min(selected + 1, last)
        : event.key === 'ArrowLeft' || event.key === 'ArrowUp'
          ? Math.max(selected - 1, 0)
          : event.key === 'Home'
            ? 0
            : event.key === 'End'
              ? last
              : null;
    if (next == null) return;
    event.preventDefault();
    setSelected(next);
    tabs.current[next]?.focus();
  }

  return (
    <div className="system-flow">
      <div className="flow-track" role="tablist" aria-label={label}>
        {stages.map((stage, index) => (
          <button
            key={stage.id}
            ref={(element) => {
              tabs.current[index] = element;
            }}
            type="button"
            role="tab"
            id={`flow-tab-${stage.id}`}
            aria-selected={index === selected}
            aria-controls="flow-panel"
            tabIndex={index === selected ? 0 : -1}
            className={`flow-step is-${stage.state}`}
            onClick={() => setSelected(index)}
            onKeyDown={move}
          >
            <span className="flow-node" aria-hidden="true">
              {index + 1}
            </span>
            <span className="flow-title">{stage.title}</span>
            <span className="flow-summary">{stage.summary}</span>
            {stage.state === 'planned' && <span className="flow-state">Planejado</span>}
          </button>
        ))}
      </div>
      <div
        key={current.id}
        className={`flow-panel is-${current.state}`}
        id="flow-panel"
        role="tabpanel"
        aria-labelledby={`flow-tab-${current.id}`}
      >
        <h3>
          {current.title}
          {current.state === 'planned' && <span className="flow-badge">Planejado</span>}
        </h3>
        {current.details.map((text) => (
          <p key={text}>{text}</p>
        ))}
        {current.planned && <p className="flow-planned">{current.planned}</p>}
        <p className="flow-who">
          <strong>Quem faz:</strong> {current.who}
        </p>
      </div>
    </div>
  );
}
