/**
 * Centralized env-var access. Throws loudly when a required var is missing
 * so we fail at test start, not deep inside a fixture.
 */

export function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(
      `Required env var ${name} is not set. ` +
        `For local runs, export it before invoking playwright. ` +
        `For Cloud Run Job runs, see e2e/README.md.`,
    );
  }
  return value;
}

export function optionalEnv(name: string, fallback: string): string {
  return process.env[name] ?? fallback;
}
