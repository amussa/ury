import qz from 'qz-tray';
import axios from 'axios';
import { KEYUTIL, KJUR, stob64, hextorstr } from 'jsrsasign';

let injectedSignKey: string | null = null;

export function initPrinting(opts: { signKey: string }) {
  injectedSignKey = opts.signKey;
}

function ensureSignKey(): string {
  if (!injectedSignKey) {
    throw new Error(
      'QZ printing has not been initialized. Call initPrinting({ signKey: ... }) before using printWithQz.'
    );
  }
  return injectedSignKey;
}

/**
 * Geometry and rendering options for thermal receipt printers.
 *
 * The defaults target an 80 mm ESC/POS printer (Xprinter POS80 class): 72 mm of
 * printable width on a 203 dpi (8 dots/mm) head. For 58 mm paper use
 * `widthMm: 48`.
 */
export interface ThermalPrintOptions {
  /** Printable width in millimetres. 80 mm paper prints ~72 mm; 58 mm paper ~48 mm. */
  widthMm?: number;
  /** Head resolution in dots per inch. Nearly every ESC/POS thermal head is 203. */
  densityDpi?: number;
  /** Character encoding for raw ESC/POS byte streams. CP860 is the Portuguese code page. */
  encoding?: string;
}

const DEFAULT_THERMAL: Required<ThermalPrintOptions> = {
  widthMm: 72,
  densityDpi: 203,
  encoding: 'IBM860',
};

export async function loadQzPrinter(host: string): Promise<void> {
  qz.security.setCertificatePromise((resolve: (data: string) => void, reject: (err?: string) => void) => {
    axios.get('/assets/ury/files/cert.pem')
      .then(({ data }) => resolve(data))
      .catch((err) => reject('Error fetching certificate: ' + String(err)));
  });
  if (!qz.websocket.isActive()) {
    await qz.websocket.connect({ host, usingSecure: false });
  }
}

export function disconnectQzPrinter(): void {
  if (qz.websocket.isActive()) qz.websocket.disconnect();
}

function applySignaturePromise(): void {
  const signKey = ensureSignKey();

  qz.security.setSignatureAlgorithm('SHA512');
  qz.security.setSignaturePromise((toSign: string) => (resolve: (sig: string) => void, reject: (err?: string) => void) => {
    try {
      const pk = KEYUTIL.getKey(signKey);
      const sig = new KJUR.crypto.Signature({ alg: 'SHA512withRSA' });
      sig.init(pk);
      sig.updateString(toSign);
      const hex = sig.sign();
      resolve(stob64(hextorstr(hex)));
    } catch (err) {
      reject(String(err));
    }
  });
}

async function withConnection(host: string, job: () => Promise<void>): Promise<void> {
  if (!qz.websocket.isActive()) {
    await loadQzPrinter(host);
  }
  await job();
}

/**
 * Print rendered HTML through QZ Tray.
 *
 * QZ rasterizes HTML into a bitmap before handing it to the printer driver, so
 * the raster parameters decide the legibility of the result. Left at their
 * defaults, QZ renders at screen density and lets the driver scale up to the
 * head's 203 dpi, which interpolates every glyph edge; the antialiased greys
 * that survive are then dithered by a head that can only burn or not burn a
 * dot. That combination is what makes receipts look smudged.
 *
 * Pinning the density to the head's real resolution, disabling content scaling
 * and forcing a 1-bit colour path removes both effects.
 *
 * For output equal to the printer's own self-test, use `printRawWithQz`
 * instead — it drives the printer's internal font rather than sending pixels.
 */
export async function printWithQz(
  host: string,
  htmlToPrint: string,
  options: ThermalPrintOptions = {}
): Promise<void> {
  const { widthMm, densityDpi } = { ...DEFAULT_THERMAL, ...options };

  applySignaturePromise();

  await withConnection(host, async () => {
    const printer = await qz.printers.getDefault();
    const data = [{ type: 'html', format: 'plain', data: htmlToPrint }];
    const config = qz.configs.create(printer, {
      size: { width: widthMm, height: null },
      units: 'mm',
      density: densityDpi,
      scaleContent: false,
      colorType: 'blackwhite',
      interpolation: 'nearest-neighbor',
      margins: 0,
    } as any);
    await qz.print(config, data as any);
  });
}

/**
 * Send a pre-rendered ESC/POS command stream straight to the printer.
 *
 * Nothing is rasterized: the byte stream selects the printer's internal font
 * and the head burns glyphs already aligned to its dot grid. Output matches the
 * quality of the printer's own Windows test page, and printing is near
 * instantaneous because no bitmap crosses the wire.
 *
 * `commands` is produced server-side by a Frappe Print Format with
 * `raw_printing` enabled.
 */
export async function printRawWithQz(
  host: string,
  commands: string,
  options: ThermalPrintOptions = {}
): Promise<void> {
  const { encoding } = { ...DEFAULT_THERMAL, ...options };

  applySignaturePromise();

  await withConnection(host, async () => {
    const printer = await qz.printers.getDefault();
    const data = [{ type: 'raw', format: 'plain', data: commands }];
    const config = qz.configs.create(printer, { encoding } as any);
    await qz.print(config, data as any);
  });
}
