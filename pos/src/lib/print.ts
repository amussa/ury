import { printWithQz, printRawWithQz } from '@ury/core';
import {
  getInvoicePrintHtml,
  getInvoiceRawCommands,
  isRawPrintFormat,
  networkPrint,
  selectNetworkPrinter,
  updatePrintStatus
} from './invoice-api';
import { PosProfileCombined } from './pos-profile-api';

interface PrintOrderParams {
  orderId: string;
  posProfile: PosProfileCombined;
  printFormat?: string | null;
  browserPrintWindow?: Window | null;
}

export async function printOrder({
  orderId,
  posProfile,
  printFormat,
  browserPrintWindow,
}: PrintOrderParams): Promise<'qz' | 'network' | 'socket'> {
  const { print_type, qz_host, print_format, printer, name, cashier, multiple_cashier } = posProfile;
  const format = printFormat || print_format;

  if (print_type === 'qz') {
    if (!qz_host) {
      throw new Error('QZ host is not set');
    }
    // A raw format drives the printer's internal font; an HTML one is
    // rasterized by QZ. Both go through QZ Tray, so only the payload differs.
    if (await isRawPrintFormat(format as string)) {
      const commands = await getInvoiceRawCommands(orderId, format as string);
      await printRawWithQz(qz_host, commands);
    } else {
      const html = await getInvoicePrintHtml(orderId, format as string);
      await printWithQz(qz_host, html);
    }
    await updatePrintStatus(orderId);
    return 'qz';
  } else if (print_type === 'network') {
    if (cashier && !multiple_cashier) {
      await networkPrint(orderId, printer as string, format as string);
    } else {
      await selectNetworkPrinter(orderId, name, format);
    }
    await updatePrintStatus(orderId);
    return 'network';
  } else {
    // A payment opens this window synchronously before its API request so
    // browsers do not block the receipt as an unsolicited popup afterwards.
    const url = `/printview?doctype=POS Invoice&name=${orderId}&format=${format}&no_letterhead=1&settings={}&letterhead=No Letterhead&trigger_print=1&_lang=en`;
    const printWindow = browserPrintWindow ?? window.open('', '_blank');
    if (!printWindow) {
      throw new Error('The receipt window was blocked by the browser');
    }
    printWindow.opener = null;
    printWindow.location.replace(url);
    await updatePrintStatus(orderId);
    return 'socket';
  }
}
