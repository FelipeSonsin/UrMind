import { useState, type FormEvent } from 'react';
import { auth, authConfigured } from '../services/auth';

/**
 * Entrar com conta. Na área da equipe (`team`) é a página inteira: o título é o h1 e o
 * texto fala só com a equipe.
 */
export function SignIn({ team = false }: { team?: boolean }) {
  const Heading = team ? 'h1' : 'h2';
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      const existing = await auth.session();
      if (
        existing?.user.is_anonymous &&
        !window.confirm(
          'Os relatos enviados sem conta não passam para a conta em que você vai entrar. Guarde o protocolo de cada relato antes de continuar. Deseja entrar?',
        )
      )
        return;
      await auth.signIn(email.trim(), password);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }

  // Variáveis VITE_SUPABASE_* ausentes no build: a pessoa só precisa saber que não dá.
  if (!authConfigured)
    return (
      <p className="error" role="alert">
        Entrar com conta não está disponível nesta versão do site.
      </p>
    );
  return (
    <form className="panel sign-in" onSubmit={submit}>
      <Heading>{team ? 'Entrar na área da equipe' : 'Entrar no UrMind'}</Heading>
      <p className="muted">
        {team
          ? 'Use a conta que a administração do UrMind liberou para você.'
          : 'Use a conta que a administração do UrMind liberou para a equipe. Para enviar um relato não é preciso entrar.'}
      </p>
      <label>
        E-mail
        <input
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
      </label>
      <label>
        Senha
        <input
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </label>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <button type="submit" disabled={busy}>
        {busy ? 'Entrando…' : 'Entrar'}
      </button>
    </form>
  );
}
