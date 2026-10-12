import { useEffect, useId, useRef, useState, type ReactNode } from 'react';

/**
 * Ação que não se desfaz com um clique (excluir rascunho, recusar, retirar do mapa,
 * marcar como repetido): o botão pergunta no próprio lugar, com o efeito escrito, antes
 * de agir. `children` entra na pergunta (por exemplo, o motivo da recusa) e
 * `confirmDisabled` segura a confirmação até ele ser preenchido. A confirmação recebe o
 * foco e é descrita pela pergunta; ao cancelar ou confirmar, o foco volta ao botão.
 * Estilos em styles/components/buttons.css (.confirm-inline).
 */
export function ConfirmButton({
  label,
  question,
  confirmLabel,
  onConfirm,
  disabled = false,
  className = 'secondary',
  confirmDisabled = false,
  display,
  onOpenChange,
  children,
}: {
  label: string;
  /** Texto do botão, quando difere do nome (por exemplo, com parte só para leitor de tela). */
  display?: ReactNode;
  /** A pergunta abriu ou fechou: quem usa pode esconder o que compete com ela. */
  onOpenChange?: (open: boolean) => void;
  question: string;
  confirmLabel: string;
  onConfirm: () => void;
  disabled?: boolean;
  className?: string;
  confirmDisabled?: boolean;
  children?: ReactNode;
}) {
  const [asking, setAsking] = useState(false);
  const confirmButton = useRef<HTMLButtonElement>(null);
  const firstField = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const returning = useRef(false);
  const questionId = useId();
  useEffect(() => {
    if (asking) {
      // Com um campo na pergunta (motivo), o foco vai para ele; senão, para a confirmação.
      const field = firstField.current?.querySelector<HTMLElement>('select, input, textarea');
      // A pergunta aparece onde estava o botão (ou na barra fixa): já está à vista, sem rolar.
      (field ?? confirmButton.current)?.focus({ preventScroll: true });
    } else if (returning.current) {
      returning.current = false;
      trigger.current?.focus({ preventScroll: true });
    }
  }, [asking]);
  const close = () => {
    returning.current = true;
    setAsking(false);
    onOpenChange?.(false);
  };
  if (!asking)
    return (
      <button
        ref={trigger}
        type="button"
        className={className}
        disabled={disabled}
        onClick={() => {
          setAsking(true);
          onOpenChange?.(true);
        }}
      >
        {display ?? label}
      </button>
    );
  return (
    <div
      className="confirm-inline"
      role="group"
      aria-label={label}
      aria-describedby={questionId}
      onKeyDown={(event) => {
        if (event.key === 'Escape') {
          event.preventDefault();
          close();
        }
      }}
    >
      <p id={questionId}>{question}</p>
      {children && <div ref={firstField}>{children}</div>}
      <div className="actions">
        <button
          ref={confirmButton}
          type="button"
          className="danger"
          aria-describedby={questionId}
          disabled={confirmDisabled || disabled}
          onClick={() => {
            close();
            onConfirm();
          }}
        >
          {confirmLabel}
        </button>
        <button type="button" className="ghost" onClick={close}>
          Cancelar
        </button>
      </div>
    </div>
  );
}
