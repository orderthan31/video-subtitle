// The unrestricted picker avoids Android's media-only photo picker path.
// This is a convenience check; the server still probes the actual uploaded bytes.
export function isVideoFile(file: Pick<File, 'name' | 'type'>): boolean {
  return file.type.toLowerCase().startsWith('video/') ||
    /\.(mp4|m4v|mkv|mov|avi|webm|mpeg|mpg|mpe|ts|mts|m2ts|3gp|3g2|wmv|flv|ogv|vob)$/i.test(file.name);
}
