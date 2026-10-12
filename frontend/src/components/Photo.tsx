import { useEffect, useState } from 'react';

/**
 * Foto guardada no aparelho; o espaço dela já existe antes de carregar (sem salto).
 * O endereço anterior da foto só é liberado depois que a imagem trocou: liberar enquanto
 * ela ainda decodifica quebra a imagem no Safari (e derruba a página no WebKit).
 */
export function Photo({ blob, alt }: { blob: Blob; alt: string }) {
  const [url, setUrl] = useState('');
  useEffect(() => {
    const next = URL.createObjectURL(blob);
    setUrl(next);
    return () => {
      window.setTimeout(() => URL.revokeObjectURL(next), 2000);
    };
  }, [blob]);
  return url ? (
    <img className="photo" src={url} alt={alt} />
  ) : (
    <span className="photo is-loading" aria-hidden="true" />
  );
}
