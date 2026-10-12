import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Check, Copy } from 'lucide-react';
import { publicSituation, publicSituationLabels, severityOf } from '../domain/public';
import { SeveritySign } from './SeveritySign';

/**
 * Componentes visuais compartilhados: marca, cabeçalho de página, estado vazio,
 * esqueleto de carregamento, selo de gravidade, botão de copiar e a trilha do relato.
 * Estilos em styles/components/ (brand, page-heading, empty-state, loading, badges,
 * buttons e situation-track).
 */

/** Símbolo do UrMind: o mesmo desenho de public/icon.svg, em linha para escalar nítido. */
export function BrandMark({ size = 28 }: { size?: number }) {
  return (
    <svg
      className="brand-mark"
      viewBox="0 0 192 192"
      width={size}
      height={size}
      aria-hidden="true"
      focusable="false"
    >
      <rect width="192" height="192" rx="42" fill="#123f36" />
      <path d="M48 48v55a48 48 0 0 0 96 0V48h-24v55a24 24 0 0 1-48 0V48z" fill="#c9f17d" />
      <circle cx="96" cy="55" r="10" fill="#fff" />
    </svg>
  );
}

/**
 * Marca completa: símbolo + nome. O arquivo PNG original tem o "ur" em branco, feito
 * para fundo escuro; aqui o nome é texto, com o mesmo contraste "ur" forte e "mind"
 * suave, legível no fundo claro.
 */
export function Brand({ size = 28 }: { size?: number }) {
  return (
    <span className="brand-lockup" style={{ fontSize: size * 0.82 }}>
      <BrandMark size={size} />
      <span className="brand-word" aria-hidden="true" translate="no">
        <b>ur</b>mind
      </span>
    </span>
  );
}

/**
 * Nome da página na aba do navegador e no histórico ("Mapa · UrMind"); o leitor de
 * tela anuncia esse título ao trocar de página. Ao sair, volta o título anterior.
 */
export function useDocumentTitle(text: string | null | undefined) {
  useEffect(() => {
    const name = text?.trim();
    if (!name) return;
    const previous = document.title;
    document.title = `${name} · UrMind`;
    return () => {
      document.title = previous;
    };
  }, [text]);
}

/** Título da página, frase de contexto e a ação principal, sempre no mesmo lugar. */
export function PageHeader({
  title,
  description,
  actions,
  back,
  children,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  back?: { href: string; label: string };
  children?: ReactNode;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  const [headingText, setHeadingText] = useState('');
  // O título pode ser texto ou elemento: o nome da aba vem do texto que aparece.
  useEffect(() => setHeadingText(heading.current?.textContent ?? ''), [title]);
  useDocumentTitle(headingText);
  return (
    <header className="page-heading">
      <div className="page-heading-text">
        {back && (
          <a className="back-link" href={back.href}>
            <span aria-hidden="true">‹</span> {back.label}
          </a>
        )}
        <h1 ref={heading}>{title}</h1>
        {description && <p>{description}</p>}
        {children}
      </div>
      {actions && <div className="page-heading-actions">{actions}</div>}
    </header>
  );
}

/** Ausência de dados como convite: o que é, por que está vazio e o que fazer. */
export function EmptyState({
  icon,
  title,
  children,
  action,
  compact = false,
}: {
  icon?: ReactNode;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
  compact?: boolean;
}) {
  return (
    <div className={`empty${compact ? ' is-compact' : ''}`}>
      {icon && <span className="empty-icon">{icon}</span>}
      <div>
        <h2>{title}</h2>
        {children && <p>{children}</p>}
      </div>
      {action && <div className="empty-action">{action}</div>}
    </div>
  );
}

/**
 * Copia um texto (protocolo, coordenadas) e confirma no próprio botão por 2 s. O aviso
 * para leitor de tela fica com quem chama (`onCopied`), como o aviso geral da página.
 */
export function CopyButton({
  text,
  label,
  onCopied,
  onFailed,
}: {
  text: string;
  label: string;
  onCopied?: () => void;
  onFailed?: () => void;
}) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 2000);
    return () => window.clearTimeout(timer);
  }, [copied]);
  return (
    <button
      type="button"
      className="secondary compact copy-button"
      data-copied={copied || undefined}
      onClick={() => {
        void navigator.clipboard.writeText(text).then(
          () => {
            setCopied(true);
            onCopied?.();
          },
          () => onFailed?.(),
        );
      }}
    >
      {copied ? <Check size={15} aria-hidden="true" /> : <Copy size={15} aria-hidden="true" />}
      {copied ? 'Copiado' : label}
    </button>
  );
}

/** Linhas cinza enquanto o conteúdo carrega; o leitor de tela não as anuncia. */
export function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="skeleton" aria-hidden="true">
      {Array.from({ length: lines }, (_, index) => (
        <span key={index} />
      ))}
    </div>
  );
}

/** Gravidade com forma e nome, nunca só a cor. */
export function SeverityTag({ severity }: { severity: string | null | undefined }) {
  const level = severityOf(severity);
  return (
    <span className={`risk-tag risk-${level.level}`}>
      <SeveritySign level={level.level} /> {level.label}
    </span>
  );
}

const TRACK = ['received', 'analyzing', 'confirmed'] as const;

/**
 * Trilha do relato: Recebido → Em análise → Confirmado. A etapa atual tem o nome
 * escrito, nunca só a cor. "Falta o local" para na primeira etapa (atenção);
 * "Não confirmado" encerra a trilha na última, sem a cor de sucesso.
 */
export function SituationTrack({
  status,
  size = 'compact',
}: {
  status: string | null | undefined;
  size?: 'compact' | 'large';
}) {
  const situation = publicSituation(status);
  const reached =
    situation === 'needs_location' ? 0 : situation === 'declined' ? 2 : TRACK.indexOf(situation);
  const tone =
    situation === 'needs_location'
      ? 'attention'
      : situation === 'declined'
        ? 'ended'
        : situation === 'confirmed'
          ? 'done'
          : 'active';
  return (
    <span
      className={`situation-track is-${size} tone-${tone}`}
      data-situation={situation}
      title={`Situação: ${publicSituationLabels[situation]}`}
    >
      <span className="track-steps" aria-hidden="true">
        {TRACK.map((step, index) => (
          <i
            key={step}
            className={index < reached ? 'is-past' : index === reached ? 'is-current' : undefined}
          />
        ))}
      </span>
      <span className="track-label">{publicSituationLabels[situation]}</span>
      {size === 'large' && (
        <ol className="track-legend" aria-label="Etapas do relato">
          {TRACK.map((step, index) => (
            <li
              key={step}
              aria-current={index === reached ? 'step' : undefined}
              className={index <= reached ? 'is-reached' : undefined}
            >
              {/* As etapas são sempre as mesmas; faltar o local aparece num aviso à parte. */}
              {situation === 'declined' && step === 'confirmed'
                ? publicSituationLabels.declined
                : publicSituationLabels[step]}
            </li>
          ))}
        </ol>
      )}
    </span>
  );
}
