export type UploadItem = {
  key: string;
  jobId?: string;
  file?: File;
  name: string;
  size: number;
  payload: Record<string, unknown>;
  createdAt: string;
  progress: number;
  state: 'waiting' | 'uploading' | 'paused' | 'error' | 'done';
  error?: string;
  started?: boolean;
};
export class UploadQueue {
  items: UploadItem[] = [];
  private running = false;
  private suspended = false;
  private disposed = false;
  private active?: {
    item: UploadItem;
    controller: AbortController;
  };
  onChange: () => void = () => {};
  private transfer: (item: UploadItem, signal: AbortSignal, changed: () => void) => Promise<void>;
  constructor(
    transfer: (item: UploadItem, signal: AbortSignal, changed: () => void) => Promise<void>,
  ) {
    this.transfer = transfer;
  }
  add(file: File, payload: Record<string, unknown>, jobId?: string) {
    if (jobId && this.items.some((item) => item.jobId === jobId && item.state !== 'done')) {
      return;
    }
    this.items.push({
      key: crypto.randomUUID(),
      file,
      name: file.name,
      size: file.size,
      payload: { ...payload },
      jobId,
      createdAt: new Date().toISOString(),
      progress: 0,
      state: 'waiting',
    });
    this.onChange();
    void this.pump();
  }
  pause(key: string) {
    const item = this.items.find((value) => value.key === key);
    if (!item || item.state === 'done') {
      return;
    }
    item.state = 'paused';
    if (this.active?.item === item) {
      this.active.controller.abort();
    }
    this.onChange();
  }
  resume(key: string) {
    const item = this.items.find((value) => value.key === key);
    if (!item?.file || !['paused', 'error'].includes(item.state)) {
      return;
    }
    item.state = 'waiting';
    item.error = undefined;
    this.onChange();
    void this.pump();
  }
  remove(key: string) {
    this.pause(key);
    this.items = this.items.filter((item) => item.key !== key);
    this.onChange();
  }
  suspend(value: boolean) {
    this.suspended = value;
    if (value) {
      this.active?.controller.abort();
    } else {
      void this.pump();
    }
  }
  dispose() {
    this.disposed = true;
    this.active?.controller.abort();
  }
  activate() {
    this.disposed = false;
    void this.pump();
  }
  forgetCompleted(ids: string[]) {
    this.items = this.items.filter(
      (item) => item.state !== 'done' || !item.jobId || !ids.includes(item.jobId),
    );
    this.onChange();
  }
  private async pump() {
    if (this.running || this.suspended || this.disposed) {
      return;
    }
    this.running = true;
    try {
      while (!this.suspended && !this.disposed) {
        const item = this.items.find((value) => value.state === 'waiting');
        if (!item) {
          break;
        }
        const controller = new AbortController();
        this.active = { item, controller };
        item.state = 'uploading';
        item.started = true;
        this.onChange();
        try {
          await this.transfer(item, controller.signal, () => this.onChange());
          if (!controller.signal.aborted) {
            item.state = 'done';
            item.file = undefined;
            item.progress = item.size;
          }
        } catch (error) {
          if (!controller.signal.aborted) {
            item.state = 'error';
            item.error = error instanceof Error ? error.message : '업로드 실패';
          }
        } finally {
          if (controller.signal.aborted && item.state === 'uploading') {
            item.state = 'waiting';
          }
          this.active = undefined;
          this.onChange();
        }
      }
    } finally {
      this.running = false;
    }
  }
}
