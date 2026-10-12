import { useEffect } from 'react';

const OPEN_MENUS = 'details.nav-more[open]';

/** O menu "Mais" do celular fecha ao trocar de página, com Escape ou com um toque fora. */
export function useMoreMenus(page: string) {
  useEffect(() => {
    document
      .querySelectorAll<HTMLDetailsElement>(OPEN_MENUS)
      .forEach((menu) => (menu.open = false));
  }, [page]);
  useEffect(() => {
    function close(event: KeyboardEvent | PointerEvent) {
      if (event instanceof KeyboardEvent && event.key !== 'Escape') return;
      document.querySelectorAll<HTMLDetailsElement>(OPEN_MENUS).forEach((menu) => {
        if (event instanceof KeyboardEvent || !menu.contains(event.target as Node)) {
          menu.open = false;
        }
      });
    }
    addEventListener('keydown', close);
    addEventListener('pointerdown', close);
    return () => {
      removeEventListener('keydown', close);
      removeEventListener('pointerdown', close);
    };
  }, []);
}
