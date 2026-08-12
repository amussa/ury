import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), 'utf8');

const app = read('src/App.tsx');
const login = read('src/components/LoginScreen.tsx');
const api = read('src/lib/waiter-api.ts');
const types = read('src/types.ts');
const menu = read('src/components/MenuView.tsx');
const editor = read('src/components/ItemEditorSheet.tsx');
const summary = read('src/components/OrderSummarySheet.tsx');
const pending = read('src/lib/pending-submission.ts');
const pwa = read('src/lib/pwa.ts');
const html = read('index.html');

assert.match(api, /loginWithUsernamePassword\(\{ username, password \}\)/);
assert.match(api, /dataset\.sessionUser/);
assert.doesNotMatch(api, /getLoggedInUser|frappe\.auth\.get_logged_user/);
assert.doesNotMatch(login, /localStorage|sessionStorage|console\.(?:log|error|warn)/);
assert.match(login, /setPassword\(''\)/);
assert.match(login, /setShowPassword\(false\)/);

assert.match(app, /title="Conta não autorizada"/);
assert.match(app, /Terminar sessão e mudar de utilizador/);
assert.match(app, /getRenderedSessionUser\(\)/);
assert.doesNotMatch(app, /getLoggedInUser|frappe\.auth\.get_logged_user/);
assert.doesNotMatch(app, /redirectToLogin|\/login\?redirect-to/);

const logoutFlow = app.slice(app.indexOf('const handleLogout'), app.indexOf("if (booting)"));
assert.match(logoutFlow, /await logoutWaiter\(\);[\s\S]*window\.location\.replace\('\/waiter'\);[\s\S]*catch/);
assert.doesNotMatch(logoutFlow, /finally\s*\{[\s\S]*window\.location\.replace/);
assert.match(logoutFlow, /A sessão atual continua ativa/);

assert.match(pwa, /beforeinstallprompt/);
assert.match(pwa, /register\('\/waiter-service-worker\.js', \{ scope: '\/waiter' \}\)/);
assert.match(html, /rel="manifest" href="\/waiter-manifest\.webmanifest"/);
assert.match(html, /rel="apple-touch-icon"/);
assert.match(html, /id="root" data-session-user="\{\{ session_user \| e \}\}"/);

assert.match(types, /price_options: WaiterPriceOption\[\]/);
assert.match(types, /price_option: string \| null/);
assert.match(api, /row\.total_available_qty \?\? row\.available_qty/);
assert.match(api, /row\.price_option \?\? row\.custom_ury_price_option/);
assert.match(app, /line\.price_option === \(option\?\.id \?\? null\)/);
assert.match(app, /maximumQuantity\(item, option\.id\)/);
assert.match(app, /price_option: line\.price_option/);
assert.match(menu, /availablePriceOptionsByCode/);
assert.match(menu, /Escolher preço/);
assert.match(editor, /Escolha o preço/);
assert.match(editor, /maximumQuantity\(option\.id\)/);
assert.match(summary, /line\.price_option_label/);
assert.match(pending, /row\.price_option/);
assert.match(pending, /item\.price_option \?\? null/);

console.log('waiter auth/PWA static checks: ok');
