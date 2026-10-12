import { CircleAlert, CircleCheck } from 'lucide-react';
import { photoProblem, type PhotoMeasure, type PhotoPolicy } from '../services/drafts';

const number = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 0 });
const decimal = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 });

function fileSize(bytes: number): string {
  const mb = bytes / (1024 * 1024);
  return mb >= 1 ? `${decimal.format(mb)} MB` : `${number.format(bytes / 1024)} kB`;
}

/**
 * Checagem da foto: o resultado em uma linha e, para quem quiser conferir, as medidas
 * com o limite de cada regra (a mesma conferência que o servidor repete no envio).
 * Estilos: styles/pages/capture.css.
 */
export function PhotoCheck({ measure, policy }: { measure: PhotoMeasure; policy: PhotoPolicy }) {
  const problem = photoProblem(measure, policy);
  const rows = [
    {
      label: 'Resolução',
      value: `${number.format(measure.width)} × ${number.format(measure.height)} px`,
      rule: `mínimo de ${policy.min_side} px no lado menor`,
    },
    {
      label: 'Luz',
      value: number.format(measure.brightness),
      rule: `aceita de ${policy.brightness_min} a ${policy.brightness_max}`,
    },
    {
      label: 'Nitidez',
      value: number.format(measure.sharpness),
      rule: `mínimo de ${policy.laplacian_min}`,
    },
    {
      label: 'Arquivo',
      value: `${fileSize(measure.bytes)}, ${measure.type.replace('image/', '').toUpperCase()}`,
      rule: 'até 10 MB',
    },
  ];
  return (
    <section className={`photo-check${problem ? ' is-off' : ''}`} aria-label="Checagem da foto">
      <p className="photo-check-result">
        {problem ? (
          <CircleAlert size={17} aria-hidden="true" />
        ) : (
          <CircleCheck size={17} aria-hidden="true" />
        )}
        <span>{problem ?? 'Foto aprovada: boa resolução, luz e nitidez.'}</span>
      </p>
      <details>
        <summary>Ver medidas</summary>
        <dl>
          {rows.map((row) => (
            <div key={row.label}>
              <dt>{row.label}</dt>
              <dd>
                <span className="photo-check-value">{row.value}</span>
                <small>{row.rule}</small>
              </dd>
            </div>
          ))}
        </dl>
        <p className="photo-check-note">O servidor repete esta checagem no envio.</p>
      </details>
    </section>
  );
}
