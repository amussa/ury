const INSTALL_AVAILABLE_EVENT = 'ury-waiter-install-available';

interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{
    outcome: 'accepted' | 'dismissed';
    platform: string;
  }>;
}

let deferredInstallPrompt: BeforeInstallPromptEvent | null = null;

function notifyInstallAvailability() {
  window.dispatchEvent(new Event(INSTALL_AVAILABLE_EVENT));
}

if (typeof window !== 'undefined') {
  window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault();
    deferredInstallPrompt = event as BeforeInstallPromptEvent;
    notifyInstallAvailability();
  });

  window.addEventListener('appinstalled', () => {
    deferredInstallPrompt = null;
    notifyInstallAvailability();
  });
}

export function isWaiterInstallAvailable(): boolean {
  return deferredInstallPrompt !== null;
}

export function subscribeToWaiterInstallAvailability(listener: () => void): () => void {
  window.addEventListener(INSTALL_AVAILABLE_EVENT, listener);
  return () => window.removeEventListener(INSTALL_AVAILABLE_EVENT, listener);
}

export async function promptWaiterInstall(): Promise<'accepted' | 'dismissed' | 'unavailable'> {
  const prompt = deferredInstallPrompt;
  if (!prompt) return 'unavailable';

  deferredInstallPrompt = null;
  notifyInstallAvailability();
  await prompt.prompt();
  const choice = await prompt.userChoice;
  return choice.outcome;
}

export function registerWaiterServiceWorker(): void {
  if (!('serviceWorker' in navigator) || !import.meta.env.PROD) return;

  window.addEventListener('load', () => {
    void navigator.serviceWorker.register('/waiter-service-worker.js', { scope: '/waiter' }).catch((error: unknown) => {
      // Installation support must never prevent the live order-entry flow.
      console.warn('Waiter service worker registration failed.', error);
    });
  }, { once: true });
}
