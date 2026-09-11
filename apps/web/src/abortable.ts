export async function abortable<T>(
  operation: (signal: AbortSignal) => Promise<T>, parent?: AbortSignal | null, timeoutMs = 120000,
): Promise<T> {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const cancel = () => controller.abort(parent?.reason);
  let onAbort: () => void = () => {};
  try {
    parent?.addEventListener('abort', cancel, {once: true});
    if (parent?.aborted) cancel();
    controller.signal.throwIfAborted();
    if (timeoutMs > 0) timer = setTimeout(() => controller.abort(
      new TypeError('업로드 연결 응답이 지연되었습니다. 잠시 후 재개해 주세요.'),
    ), timeoutMs);
    const cancelled = new Promise<never>((_, reject) => {
      onAbort = () => reject(controller.signal.reason);
      controller.signal.addEventListener('abort', onAbort, {once: true});
    });
    return await Promise.race([cancelled, operation(controller.signal)]);
  } finally {
    clearTimeout(timer);
    parent?.removeEventListener('abort', cancel);
    controller.signal.removeEventListener('abort', onAbort);
  }
}
