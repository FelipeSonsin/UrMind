import { Component, StrictMode, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
// Public Sans: uma família só, de títulos a legendas (fonte livre, OFL).
import '@fontsource-variable/public-sans';
// Estilos: tokens e um arquivo por camada, componente e página (ordem em index.css).
import './styles/index.css';

class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? (
      <main className="panel narrow crash">
        <h1>Não foi possível abrir esta tela</h1>
        <p>
          Recarregue o aplicativo para tentar novamente. Os rascunhos já salvos permanecem no
          navegador.
        </p>
        <button onClick={() => location.reload()}>Recarregar</button>
      </main>
    ) : (
      this.props.children
    );
  }
}
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
);
