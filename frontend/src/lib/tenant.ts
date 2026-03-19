/**
 * Tenant utilities — single-tenant no-ops for Pablo Solo.
 *
 * The multi-tenant SaaS uses these to resolve and cache Firebase tenant IDs.
 * In solo mode there is no tenant isolation, so all functions are stubs.
 */

export function getCachedTenantId(): string | null {
  return null
}

export function setCachedTenantId(_tenantId: string): void {}

export function clearCachedTenantId(): void {}

export async function resolveTenant(
  _email: string,
  _apiUrl: string,
): Promise<string | null> {
  return null
}
