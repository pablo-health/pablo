/**
 * TOTP helpers, matching `oathtool --totp -b` semantics used in the pentest
 * playbook (.claude/skills/pentest/SKILL.md).
 *
 * Firebase rejects replayed TOTP codes. Use waitForFreshWindow() before
 * generating a code that will be consumed in a flow with risk of replay
 * (e.g., enrolling then immediately re-signing-in).
 */
import { authenticator } from "otplib";

authenticator.options = { step: 30, window: 0 };

export function generateTotp(secret: string): string {
  return authenticator.generate(secret);
}

/**
 * Wait until at least `minRemainingSeconds` remain in the current 30s window.
 * Default 3s gives the network call time to land before the code expires.
 */
export async function waitForFreshWindow(
  minRemainingSeconds = 3,
): Promise<void> {
  while (true) {
    const remaining = 30 - (Math.floor(Date.now() / 1000) % 30);
    if (remaining >= minRemainingSeconds) return;
    await new Promise((r) => setTimeout(r, 500));
  }
}

/**
 * Wait until the start of a fresh TOTP window, then return a code.
 * Matches the pentest pattern:
 *   until [ $((30 - $(date +%s) % 30)) -lt 3 ]; do sleep 2; done
 *   sleep 3
 *   oathtool --totp -b "$SECRET"
 */
export async function freshTotp(secret: string): Promise<string> {
  // Wait until we're inside the last 3s of the current window, then 3s into the next
  while (30 - (Math.floor(Date.now() / 1000) % 30) >= 3) {
    await new Promise((r) => setTimeout(r, 500));
  }
  await new Promise((r) => setTimeout(r, 3000));
  return generateTotp(secret);
}
