import { FormEvent, useRef, useState } from 'react';
import { Eye, EyeOff, LoaderCircle, LockKeyhole, UserRound } from 'lucide-react';
import { InstallAppButton } from '@/components/InstallAppButton';
import { loginWaiter } from '@/lib/waiter-api';

interface LoginScreenProps {
  onAuthenticated: () => void;
}

export function LoginScreen({ onAuthenticated }: LoginScreenProps) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const passwordInputRef = useRef<HTMLInputElement>(null);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submitting) return;

    const normalizedUsername = username.trim();
    if (!normalizedUsername || !password) {
      setError('Introduza o utilizador e a palavra-passe.');
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      const result = await loginWaiter(normalizedUsername, password);
      setPassword('');
      if (result.requiresVerification) {
        setError('Esta conta exige uma verificação adicional que não está disponível no atendimento móvel. Contacte o administrador.');
        return;
      }
      onAuthenticated();
    } catch {
      // Keep authentication errors generic and never retain the submitted password.
      setPassword('');
      setError('Não foi possível iniciar sessão. Confirme o utilizador e a palavra-passe.');
      window.requestAnimationFrame(() => passwordInputRef.current?.focus());
    } finally {
      setShowPassword(false);
      setSubmitting(false);
    }
  };

  return (
    <main className="safe-top safe-bottom flex min-h-dvh min-w-[320px] items-center justify-center bg-slate-50 px-4 py-8">
      <section className="w-full max-w-sm rounded-3xl border border-slate-200 bg-white p-5 shadow-xl shadow-slate-200/60 sm:p-7" aria-labelledby="waiter-login-title">
        <div className="text-center">
          <img
            className="mx-auto h-20 w-20 rounded-3xl object-cover shadow-sm"
            src="/assets/ury/waiter/icons/icon-192.png"
            alt="Gelatiamo"
            width="80"
            height="80"
          />
          <p className="mt-4 text-xs font-bold uppercase tracking-[0.2em] text-blue-700">URY</p>
          <h1 id="waiter-login-title" className="mt-1 text-2xl font-bold text-slate-950">Atendimento de mesa</h1>
          <p className="mt-2 text-sm leading-6 text-slate-600">Inicie sessão para consultar as mesas e registar pedidos.</p>
        </div>

        <form className="mt-7 space-y-4" noValidate aria-busy={submitting} onSubmit={(event) => void submit(event)}>
          <div>
            <label className="mb-1.5 block text-sm font-semibold text-slate-800" htmlFor="waiter-username">Utilizador</label>
            <div className="relative">
              <UserRound className="pointer-events-none absolute left-3.5 top-1/2 h-5 w-5 -translate-y-1/2 text-slate-400" aria-hidden="true" />
              <input
                id="waiter-username"
                name="username"
                type="text"
                autoComplete="username"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                autoFocus
                enterKeyHint="next"
                className="h-12 w-full rounded-xl border border-slate-300 bg-white pl-11 pr-3 text-base text-slate-950 outline-none transition placeholder:text-slate-400 focus:border-primary focus:ring-2 focus:ring-blue-100 disabled:bg-slate-100"
                placeholder="Nome de utilizador"
                value={username}
                disabled={submitting}
                required
                aria-invalid={Boolean(error)}
                aria-describedby={error ? 'waiter-login-error' : undefined}
                onChange={(event) => {
                  setUsername(event.target.value);
                  if (error) setError(null);
                }}
              />
            </div>
          </div>

          <div>
            <label className="mb-1.5 block text-sm font-semibold text-slate-800" htmlFor="waiter-password">Palavra-passe</label>
            <div className="relative">
              <LockKeyhole className="pointer-events-none absolute left-3.5 top-1/2 h-5 w-5 -translate-y-1/2 text-slate-400" aria-hidden="true" />
              <input
                ref={passwordInputRef}
                id="waiter-password"
                name="password"
                type={showPassword ? 'text' : 'password'}
                autoComplete="current-password"
                enterKeyHint="go"
                className="h-12 w-full rounded-xl border border-slate-300 bg-white pl-11 pr-12 text-base text-slate-950 outline-none transition placeholder:text-slate-400 focus:border-primary focus:ring-2 focus:ring-blue-100 disabled:bg-slate-100"
                placeholder="Palavra-passe"
                value={password}
                disabled={submitting}
                required
                aria-invalid={Boolean(error)}
                aria-describedby={error ? 'waiter-login-error' : undefined}
                onChange={(event) => {
                  setPassword(event.target.value);
                  if (error) setError(null);
                }}
              />
              <button
                type="button"
                className="absolute right-1 top-1/2 grid h-10 w-10 -translate-y-1/2 place-items-center rounded-lg text-slate-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                aria-label={showPassword ? 'Ocultar palavra-passe' : 'Mostrar palavra-passe'}
                aria-pressed={showPassword}
                disabled={submitting}
                onClick={() => setShowPassword((visible) => !visible)}
              >
                {showPassword
                  ? <EyeOff className="h-5 w-5" aria-hidden="true" />
                  : <Eye className="h-5 w-5" aria-hidden="true" />}
              </button>
            </div>
          </div>

          {error ? (
            <p id="waiter-login-error" className="rounded-xl border border-red-200 bg-red-50 px-3 py-2.5 text-sm font-medium leading-5 text-red-800" role="alert">
              {error}
            </p>
          ) : null}

          <button
            type="submit"
            className="flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-primary px-4 text-base font-bold text-white shadow-sm transition hover:bg-blue-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 disabled:opacity-60"
            disabled={submitting}
          >
            {submitting ? <LoaderCircle className="h-5 w-5 animate-spin" aria-hidden="true" /> : null}
            {submitting ? 'A iniciar sessão…' : 'Entrar'}
          </button>
        </form>

        <InstallAppButton className="mt-3" />
        <p className="mt-5 text-center text-xs leading-5 text-slate-500">Use apenas a sua conta individual de atendente.</p>
      </section>
    </main>
  );
}
