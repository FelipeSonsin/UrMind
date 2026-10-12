import { useEffect, useState } from 'react';

/**
 * Tela larga (700 px ou mais): filtros sempre à vista. Abaixo disso, as listas da equipe
 * recolhem os filtros atrás de "Filtros", para a lista aparecer primeiro.
 */
export function useWideScreen(): boolean {
  const query = '(min-width: 700px)';
  const [wide, setWide] = useState(() => matchMedia(query).matches);
  useEffect(() => {
    const list = matchMedia(query);
    const update = () => setWide(list.matches);
    list.addEventListener('change', update);
    return () => list.removeEventListener('change', update);
  }, []);
  return wide;
}
