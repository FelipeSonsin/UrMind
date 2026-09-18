import { useState, type FormEvent } from 'react';
import { auth, authConfigured } from '../services/auth';

export function SignIn() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      await auth.signIn(email.trim(), password);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!authConfigured)
    return (
      <p className="error" role="alert">
        Autenticação não configurada: defina VITE_SUPABASE_URL e VITE_SUPABASE_PUBLISHABLE_KEY.
      </p>
    );
  return (
    <form className="panel sign-in" onSubmit={submit}>
      <h2>Entrar no UrMind</h2>
      <p className="muted">
        Envio de evidências, ocorrências e revisão exigem uma conta autorizada.
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
