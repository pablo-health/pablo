/**
 * Console-error guard for smoke specs.
 *
 * Listens for browser-side failures that should fail a smoke run:
 *   - console.error()      — React error boundary trips, hydration
 *                            mismatches, uncaught promise rejections
 *                            React surfaces via console, etc.
 *   - pageerror            — uncaught JS exceptions on the page
 *
 * Why this exists: the deploy pipeline caught us twice with regressions
 * (pablo#253 ToastProvider missing, pablo#255 chat eager-drain) that
 * rendered the page "fine enough" to pass shallow specs but logged a
 * real error the user would notice. Cheap console listening turns those
 * into hard failures.
 *
 * Allowlist policy: empty. Add patterns here only when a real false
 * positive surfaces — never to make a flaky spec green.
 */
import type { Page } from "@playwright/test";

const ALLOWLIST: RegExp[] = [
  // Browser-emitted resource-fetch errors. Chromium logs every failed
  // network response at console.error level — these are duplicative
  // with whatever the application's response handler does. If the app
  // is actually broken by a 401/403/5xx, the JS-side handler logs its
  // own (distinct) message and that still trips the guard. Without
  // this filter, smoke specs flake on any background refetch that
  // races sign-in.
  /^Failed to load resource: /,
];

export type ConsoleGuard = {
  /** All non-allowlisted console.error messages observed since attach. */
  errors: string[];
  /** All uncaught page exceptions observed since attach. */
  pageErrors: Error[];
  /** Detach the listeners. */
  dispose: () => void;
};

/**
 * Attach error listeners to a Page. Returns a guard whose `errors` /
 * `pageErrors` arrays accumulate across navigations until `dispose()`.
 * Designed to be checked at the END of a route visit so a single guard
 * can cover a multi-route walk.
 */
export function attachConsoleGuard(page: Page): ConsoleGuard {
  const errors: string[] = [];
  const pageErrors: Error[] = [];

  const onConsole = (msg: import("@playwright/test").ConsoleMessage) => {
    if (msg.type() !== "error") return;
    const text = msg.text();
    if (ALLOWLIST.some((re) => re.test(text))) return;
    errors.push(text);
  };
  const onPageError = (err: Error) => {
    pageErrors.push(err);
  };

  page.on("console", onConsole);
  page.on("pageerror", onPageError);

  return {
    errors,
    pageErrors,
    dispose: () => {
      page.off("console", onConsole);
      page.off("pageerror", onPageError);
    },
  };
}
