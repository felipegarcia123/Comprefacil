export interface HardwareService {
  printReceipt(receipt: unknown): Promise<void>;
  openCashDrawer(): Promise<void>;
  scanBarcode(): Promise<string | null>;
}

export class MockHardwareService implements HardwareService {
  async printReceipt(receipt: unknown) { console.info("[Mock receipt]", receipt); }
  async openCashDrawer() { console.info("[Mock cash drawer] opened"); }
  async scanBarcode() { return null; }
}

