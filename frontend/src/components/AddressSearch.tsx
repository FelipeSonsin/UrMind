import { useEffect, useRef, useState } from 'react';
import { Search } from 'lucide-react';
import { publicApi, type AddressResult } from '../services/publicApi';

/**
 * Busca de endereço ou CEP para levar o alvo do mapa até o lugar (no envio do relato e em
 * "Seu relato" sem local). Busca só quando a pessoa pede (botão ou Enter): nada de consulta a
 * cada tecla, e o servidor respeita o limite do serviço de endereços.
 */
export function AddressSearch({
  onChoose,
}: {
  /** Endereço escolhido: o ponto vale e o mapa leva o alvo até ele. */
  onChoose: (point: { latitude: number; longitude: number }) => void;
}) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<AddressResult[] | null>(null);
  const [attribution, setAttribution] = useState('');
  const [error, setError] = useState('');
  const [searching, setSearching] = useState(false);
  const [chosen, setChosen] = useState<number | null>(null);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);

  async function search() {
    const text = query.trim();
    if (text.length < 3 || searching) return;
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setSearching(true);
    setError('');
    try {
      const found = await publicApi.searchAddress(text, current.signal);
      if (current.signal.aborted) return;
      setResults(found.results);
      setAttribution(found.attribution);
      setChosen(null);
    } catch (reason) {
      if (!current.signal.aborted) {
        setResults(null);
        setError(reason instanceof Error ? reason.message : 'Busca de endereço indisponível.');
      }
    } finally {
      if (!current.signal.aborted) setSearching(false);
    }
  }

  return (
    <>
      <div className="address-search" role="search">
        <label>
          Buscar endereço ou CEP
          <input
            type="search"
            value={query}
            maxLength={200}
            autoComplete="off"
            placeholder="Ex.: Rua Galvão Bueno, 100, São Paulo…"
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                // Enter busca o endereço; nunca envia o formulário.
                event.preventDefault();
                void search();
              }
            }}
          />
        </label>
        <button
          type="button"
          className="secondary"
          disabled={searching || query.trim().length < 3}
          onClick={() => void search()}
        >
          <Search size={16} aria-hidden="true" /> {searching ? 'Buscando…' : 'Buscar'}
        </button>
      </div>
      {query.trim().length > 0 && query.trim().length < 3 && (
        <p className="field-note">Digite pelo menos 3 letras para buscar.</p>
      )}
      {error && (
        <p className="field-error" role="alert">
          {error}
        </p>
      )}
      {results &&
        (results.length ? (
          <ul className="address-results" aria-label="Endereços encontrados">
            {results.map((result, index) => (
              <li key={`${result.latitude},${result.longitude},${index}`}>
                <button
                  type="button"
                  aria-pressed={chosen === index}
                  onClick={() => {
                    setChosen(index);
                    onChoose({ latitude: result.latitude, longitude: result.longitude });
                  }}
                >
                  {result.label}
                  {result.detail && <small>{result.detail}</small>}
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="address-hint" role="status">
            Nenhum endereço encontrado. Tente com a rua e a cidade, ou marque no mapa.
          </p>
        ))}
      {attribution && <p className="map-caption">Busca de endereço: {attribution}</p>}
      <p className="address-hint">
        {chosen != null
          ? 'Confira se o alvo está sobre o local exato; se não estiver, arraste o mapa.'
          : 'Ou arraste o mapa até o alvo ficar sobre o local do problema.'}
      </p>
    </>
  );
}
