/**
 * Tenant utilities — single-tenant no-ops for Pablo Core.
 *
 * The multi-tenant SaaS uses these to resolve and cache Firebase tenant IDs.
 * In core mode there is no tenant isolation, so all functions are stubs.
 */

export interface ResolveTenantResult {
  tenantId: string | null
  isAdmin: boolean
}

export function getCachedTenantId(): string | null {
  return null
}

export function setCachedTenantId(_tenantId: string): void {}

export function clearCachedTenantId(): void {}

export async function resolveTenant(
  _email: string,
  _apiUrl: string,
): Promise<ResolveTenantResult> {
  return { tenantId: null, isAdmin: false }
}

export async function signupPractice(
  _email: string,
  _practiceName: string,
  _apiUrl: string,
): Promise<string | null> {
  return null
}
