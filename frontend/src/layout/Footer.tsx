import { useAppState } from '../app/AppState';

/** Rodapé do site do cidadão: os links com o mesmo nome das páginas. A área da equipe não tem rodapé. */
export function Footer() {
  const { adminSurface } = useAppState();
  if (adminSurface) return null;
  return (
    <footer>
      <a href="#/sobre">Sobre e privacidade</a>
      <a href="#/transparency">Como funciona</a>
      <a href="#/system">Situação do serviço</a>
    </footer>
  );
}
