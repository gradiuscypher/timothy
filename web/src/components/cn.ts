/** Join class names, dropping anything falsy. The `cn` shadcn components expect. */
export function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}
