// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { IDBFactory } from "fake-indexeddb"

import { clearFirebaseAuthStorage } from "../firebaseAuthRecovery"

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
})
