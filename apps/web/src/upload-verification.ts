export const blockSize = 4 * 1024 * 1024;
export type VerificationBlock = {
  uploaded_bytes: number;
  offset: number;
  length: number;
  sha256: string;
};
export const verifyUploadedPrefix = async (
  file: Blob,
  uploaded: number,
  signal: AbortSignal,
  verify: (block: VerificationBlock) => Promise<void>,
) => {
  if (!Number.isSafeInteger(uploaded) || uploaded < 0 || uploaded > file.size) {
    throw new Error('서버의 업로드 크기가 선택한 파일과 일치하지 않습니다.');
  }
  for (let offset = 0; offset < uploaded; offset += blockSize) {
    signal.throwIfAborted();
    const buffer = await file.slice(offset, Math.min(uploaded, offset + blockSize)).arrayBuffer();
    const digest = await crypto.subtle.digest('SHA-256', buffer);
    signal.throwIfAborted();
    const sha256 = Array.from(new Uint8Array(digest), (n) => n.toString(16).padStart(2, '0')).join(
      '',
    );
    await verify({ uploaded_bytes: uploaded, offset, length: buffer.byteLength, sha256 });
  }
};
