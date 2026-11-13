// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { IDBFactory } from "fake-indexeddb"

import { CLEAR_AUTH_STORAGE_LIMIT_MS, clearFirebaseAuthStorage } from "../firebaseAuthRecovery"

// A real IndexedDB implementation, not a mock: what matters here is how the
// database behaves while another connection holds it open, which is exactly
// the part a mock would have to assume.

const DB = "firebaseLocalStorageDb"
const STORE = "firebaseLocalStorage"
const USER_KEY = "firebase:authUser:key:[DEFAULT]"

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB, 1)
    request.onupgradeneeded = () => {
      request.result.createObjectStore(STORE, { keyPath: "fbase_key" })
    }
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}

function put(db: IDBDatabase, value: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const tx = db.transaction([STORE], "readwrite")
    tx.objectStore(STORE).put({ fbase_key: USER_KEY, value })
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error)
  })
}

function read(db: IDBDatabase): Promise<string | null> {
  return new Promise((resolve, reject) => {
    const request = db.transaction([STORE]).objectStore(STORE).get(USER_KEY)
    request.onsuccess = () => resolve(request.result ? request.result.value : null)
    request.onerror = () => reject(request.error)
  })
}

/** Let any queued IndexedDB work (a postponed delete, say) run. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 50))

describe("clearFirebaseAuthStorage", () => {
  beforeEach(() => {
    globalThis.indexedDB = new IDBFactory()
  })

  afterEach(() => {
    // @ts-expect-error -- happy-dom provides none; restore that.
    delete globalThis.indexedDB
  })

  it("removes the stored session even while the SDK holds the database open", async () => {
    const sdk = await open()
    await put(sdk, "stale-session")

    await clearFirebaseAuthStorage()

    expect(await read(sdk)).toBeNull()
    sdk.close()
  })

  // The login screen wipes storage after a forced logout, with this page's
  // own Firebase connection open. A delete blocked by that connection was
  // only postponed: it ran when the connection closed — the next page
  // unload — and took the sign-in made in between with it.
  it("leaves nothing behind that later wipes a fresh sign-in", async () => {
    const sdk = await open()
    await put(sdk, "stale-session")

    await clearFirebaseAuthStorage()
    await put(sdk, "fresh-session")
    sdk.close() // the page unloads
    await settle()

    const nextPage = await open()
    expect(await read(nextPage)).toBe("fresh-session")
    nextPage.close()
  })

  it("does not create the database when there is none", async () => {
    await clearFirebaseAuthStorage()
    const names = (await indexedDB.databases()).map((db) => db.name)
    expect(names).not.toContain(DB)
  })

  // Another tab of the app holds a transaction on the database and never
  // finishes it (a browser can suspend a tab mid-transaction). The clear
  // queues behind it for as long as it is held, so the reset's reload never
  // came. Held here the way such a tab holds it: one request after another
  // on the store, so the transaction never runs out of work.
  it("resolves within its limit while another tab holds the database", async () => {
    const otherTab = await open()
    await put(otherTab, "stale-session")
    let holding = true
    const hold = otherTab.transaction([STORE], "readwrite")
    const store = hold.objectStore(STORE)
    const again = () => {
      if (holding) store.get(USER_KEY).onsuccess = again
    }
    again()

    const started = Date.now()
    await clearFirebaseAuthStorage()
    const elapsed = Date.now() - started

    expect(elapsed).toBeGreaterThanOrEqual(CLEAR_AUTH_STORAGE_LIMIT_MS - 50)
    expect(elapsed).toBeLessThan(CLEAR_AUTH_STORAGE_LIMIT_MS + 1_000)
    holding = false
    otherTab.close()
  }, 10_000)
})

/**
 * An open that never fires success or error, as one queued behind another
 * tab's pending delete does. Hand-built: the point is a request that never
 * answers, or answers only when the test says so.
 */
function stalledOpen() {
  const request: Partial<IDBOpenDBRequest> & { result?: IDBDatabase } = {}
  const db = {
    objectStoreNames: [] as unknown as DOMStringList,
    close: vi.fn(),
  }
  const factory = { open: vi.fn(() => request as IDBOpenDBRequest) }
  /** Let the open succeed now. */
  const succeed = () => {
    request.result = db as unknown as IDBDatabase
    request.onsuccess?.call(request as IDBOpenDBRequest, new Event("success"))
  }
  return { factory, db, succeed }
}

describe("clearFirebaseAuthStorage with an open that never answers", () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    // @ts-expect-error -- happy-dom provides none; restore that.
    delete globalThis.indexedDB
  })

  it("resolves at its limit", async () => {
    const { factory } = stalledOpen()
    globalThis.indexedDB = factory as unknown as IDBFactory
    let done = false
    void clearFirebaseAuthStorage().then(() => {
      done = true
    })

    await vi.advanceTimersByTimeAsync(CLEAR_AUTH_STORAGE_LIMIT_MS - 1)
    expect(done).toBe(false)
    await vi.advanceTimersByTimeAsync(1)
    expect(done).toBe(true)
  })

  it("closes a connection that opens after the limit", async () => {
    const { factory, db, succeed } = stalledOpen()
    globalThis.indexedDB = factory as unknown as IDBFactory
    const cleared = clearFirebaseAuthStorage()
    await vi.advanceTimersByTimeAsync(CLEAR_AUTH_STORAGE_LIMIT_MS)
    await cleared

    succeed()
    expect(db.close).toHaveBeenCalledTimes(1)
  })
})
