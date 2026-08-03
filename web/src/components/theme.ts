import { useCallback, useEffect, useState } from "react";

/**
 * The reader's choice of theme, and the two places it is written down.
 *
 * A theme here is two independent choices rather than one from a list of four: a family,
 * which decides typeface and geometry as much as colour, and a mode, which decides only
 * whether the scale runs light or dark. They are independent because they genuinely are —
 * industrial light and industrial dark are one design at two polarities, not two designs —
 * and because "follow the system" then composes with every family for free instead of
 * doubling the list every time a family is added.
 *
 * `data-mode` on `<html>` is always `light` or `dark`. "system" is a stored preference,
 * never an attribute: CSS cannot express "the reader asked for the system's answer", so
 * the question is resolved here and in the inline script in `index.html`, and the
 * stylesheet only ever sees the answer. That is what keeps `styles.css` free of media
 * queries, and it is why a `matchMedia` listener lives below — somebody on "system" who
 * changes their OS theme with this open should see it change, not see it next time.
 */

export const FAMILIES = ["default", "industrial"] as const;
export const MODES = ["system", "light", "dark"] as const;

export type Family = (typeof FAMILIES)[number];
/** What the reader chose. */
export type Mode = (typeof MODES)[number];
/** What that resolves to, and the only thing `<html>` ever carries. */
export type ResolvedMode = "light" | "dark";

export const FAMILY_KEY = "timothy.theme.family";
export const MODE_KEY = "timothy.theme.mode";

export const DEFAULT_FAMILY: Family = "default";
/** The behaviour the app had before it had a selector: whatever the OS says. */
export const DEFAULT_MODE: Mode = "system";

const isFamily = (value: unknown): value is Family =>
  FAMILIES.includes(value as Family);
const isMode = (value: unknown): value is Mode => MODES.includes(value as Mode);

/**
 * Storage can throw rather than merely be empty — a browser with cookies and site data
 * blocked raises on the property access itself — and a theme is not worth a blank screen.
 */
function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Nothing to do and nothing worth saying: the theme applies for this page either way.
  }
}

export function storedFamily(): Family {
  const value = read(FAMILY_KEY);
  return isFamily(value) ? value : DEFAULT_FAMILY;
}

export function storedMode(): Mode {
  const value = read(MODE_KEY);
  return isMode(value) ? value : DEFAULT_MODE;
}

const DARK = "(prefers-color-scheme: dark)";

/**
 * jsdom does not implement `matchMedia`, and neither does any other non-browser host, so
 * this has to cope with its absence rather than assume it. `lib.dom` types it as always
 * present, so the check has to be a runtime one on the object rather than a test the
 * compiler believes it can answer.
 */
export function mediaQuery(query: string): MediaQueryList | null {
  if (!("matchMedia" in window)) return null;
  return window.matchMedia(query);
}

/** Absent means light, which is the CSS default for a client with no preference. */
export function prefersDark(): boolean {
  return mediaQuery(DARK)?.matches ?? false;
}

export function resolveMode(mode: Mode): ResolvedMode {
  if (mode === "system") return prefersDark() ? "dark" : "light";
  return mode;
}

export function applyTheme(family: Family, mode: Mode): void {
  const root = document.documentElement;
  const resolved = resolveMode(mode);
  root.dataset.family = family;
  root.dataset.mode = resolved;
  // `color-scheme` is what decides the colour of scrollbars, form controls and the canvas
  // behind the app, none of which this stylesheet paints. The theme blocks in
  // `styles.css` declare it too, for a browser running no JavaScript; setting it inline
  // as well is what makes it correct in the moment before that stylesheet has loaded.
  root.style.colorScheme = resolved;
}

export function useTheme() {
  const [family, setFamilyState] = useState<Family>(storedFamily);
  const [mode, setModeState] = useState<Mode>(storedMode);

  useEffect(() => {
    applyTheme(family, mode);
  }, [family, mode]);

  // Only "system" is listening. The other two modes are an answer already given, and
  // re-applying on every OS change would be a no-op with a subscription attached to it.
  useEffect(() => {
    if (mode !== "system") return;
    const query = mediaQuery(DARK);
    if (!query) return;
    const onChange = () => applyTheme(family, "system");
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, [family, mode]);

  const setFamily = useCallback((next: Family) => {
    write(FAMILY_KEY, next);
    setFamilyState(next);
  }, []);

  const setMode = useCallback((next: Mode) => {
    write(MODE_KEY, next);
    setModeState(next);
  }, []);

  return { family, mode, setFamily, setMode };
}

export const FAMILY_LABELS: Record<Family, string> = {
  default: "Default",
  industrial: "Industrial",
};

export const MODE_LABELS: Record<Mode, string> = {
  system: "System",
  light: "Light",
  dark: "Dark",
};
