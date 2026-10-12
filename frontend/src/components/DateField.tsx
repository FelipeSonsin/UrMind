import { useState } from 'react';
import { formatBrDate, maskBrDate, parseBrDate } from '../domain/public';

/**
 * Data digitada como dd/mm/aaaa: o campo nativo de data segue o idioma do navegador e
 * mostraria mm/dd/yyyy numa interface em português. Guarda aaaa-mm-dd internamente.
 */
export function DateField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (isoDate: string) => void;
}) {
  const [text, setText] = useState(() => formatBrDate(value));
  const [lastValue, setLastValue] = useState(value);
  if (value !== lastValue) {
    setLastValue(value);
    setText(formatBrDate(value));
  }
  const invalid = text.length === 10 && parseBrDate(text) === null;
  return (
    <label>
      {label}
      <input
        inputMode="numeric"
        autoComplete="off"
        placeholder="dd/mm/aaaa"
        maxLength={10}
        value={text}
        aria-invalid={invalid || undefined}
        onChange={(event) => {
          const next = maskBrDate(event.target.value);
          setText(next);
          if (!next) onChange('');
          else {
            const iso = parseBrDate(next);
            if (iso) onChange(iso);
          }
        }}
      />
      {invalid && <small className="field-error">Data inválida: use dd/mm/aaaa</small>}
    </label>
  );
}
