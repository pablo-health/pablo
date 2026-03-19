import "@testing-library/jest-dom"
import { vi } from "vitest"

// Mock the /api/config endpoint for runtime configuration
global.fetch = vi.fn((url) => {
  if (url === '/api/config' || url.toString().endsWith('/api/config')) {
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({
        apiUrl: 'http://localhost:8000',
        devMode: true,
        dataMode: 'mock',
        enableLocalAuth: false,
        firebaseProjectId: 'test-project',
      }),
    } as Response)
  }
  return Promise.resolve({
    ok: true,
    json: () => Promise.resolve({}),
  } as Response)
}) as unknown as typeof fetch
