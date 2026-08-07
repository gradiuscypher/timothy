import { createContext, useCallback, useContext, useRef, useState } from "react";
import type { ReactNode } from "react";

import { cn } from "./cn";

type Tone = "ok" | "danger" | "warn";

interface ToastEntry {
  id: number;
  message: string;
  tone: Tone;
}

const TONE_CLASSES: Record<Tone, string> = {
  ok: "border-ok/40 bg-ok/10 text-ok",
  danger: "border-danger/40 bg-danger/10 text-danger",
  warn: "border-warn/40 bg-warn/10 text-warn",
};

const DURATION_MS = 4_000;

const ToastContext = createContext<((message: string, tone?: Tone) => void) | null>(null);

/**
 * A confirmation that fades on its own.
 *
 * Every save on these screens already shows itself — the form clears, the table
 * re-renders — but that's easy to miss on a slow connection or when the moderator isn't
 * looking straight at the card. This is the same fact, said once, out loud.
 */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastEntry[]>([]);
  const nextId = useRef(0);

  const notify = useCallback((message: string, tone: Tone = "ok") => {
    const id = nextId.current++;
    setToasts((current) => [...current, { id, message, tone }]);
    setTimeout(() => {
      setToasts((current) => current.filter((toast) => toast.id !== id));
    }, DURATION_MS);
  }, []);

  return (
    <ToastContext.Provider value={notify}>
      {children}
      <div
        role="status"
        aria-live="polite"
        className="fixed right-4 bottom-4 z-50 flex flex-col gap-2"
      >
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={cn(
              "rounded-md border px-3 py-2 text-sm shadow-lg",
              TONE_CLASSES[toast.tone],
            )}
          >
            {toast.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

/** `notify("Channel set.")` from anywhere under `ToastProvider`. */
export function useToast(): (message: string, tone?: Tone) => void {
  const notify = useContext(ToastContext);
  if (!notify) throw new Error("useToast must be used within a ToastProvider");
  return notify;
}
