/** Tiny class-name joiner (no dependency needed). */
export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}
