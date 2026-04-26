import { create } from 'zustand'
import { openUrl } from '@tauri-apps/plugin-opener'

import { exchangeAuth, getConfigStatus, setAuthTokenProvider, startAuth } from '../api/client'
import type { ConfigStatus } from '../types'

const PENDING_SESSION_KEY = 'aftertaste.pendingAuthSessionId'
const CLOUD_BEARER_TOKEN_KEY = 'aftertaste.cloudBearerToken'
let deepLinkListenerReady = false
let fallbackCloudToken: string | null = null

function readPendingSession() {
  if (typeof window === 'undefined') {
    return null
  }
  return window.localStorage.getItem(PENDING_SESSION_KEY)
}

function writePendingSession(sessionId: string | null) {
  if (typeof window === 'undefined') {
    return
  }
  if (!sessionId) {
    window.localStorage.removeItem(PENDING_SESSION_KEY)
    return
  }
  window.localStorage.setItem(PENDING_SESSION_KEY, sessionId)
}

function readCloudBearerToken() {
  if (typeof window === 'undefined') {
    return null
  }
  return window.localStorage.getItem(CLOUD_BEARER_TOKEN_KEY)
}

function writeCloudBearerToken(token: string | null) {
  if (typeof window === 'undefined') {
    return
  }
  if (!token) {
    window.localStorage.removeItem(CLOUD_BEARER_TOKEN_KEY)
    return
  }
  window.localStorage.setItem(CLOUD_BEARER_TOKEN_KEY, token)
}

function applyCloudTokenFallback(token: string | null) {
  fallbackCloudToken = token
  if (!token) {
    return
  }
  setAuthTokenProvider(async () => fallbackCloudToken)
}

type AuthState = {
  status: ConfigStatus | null
  pendingSessionId: string | null
  pendingAuthUrl: string | null
  loading: boolean
  error: string | null
  refreshStatus: () => Promise<void>
  beginAuth: () => Promise<void>
  completeAuth: (callbackUrl: string) => Promise<void>
  initDeepLinkListener: () => Promise<void>
}

function parseCallbackUrl(input: string): { code: string; state: string } {
  const trimmed = input.trim()
  const url = trimmed.includes('://') ? new URL(trimmed) : new URL(`http://x?${trimmed}`)

  const code = url.searchParams.get('code')
  const state = url.searchParams.get('state')

  if (!code || !state) {
    throw new Error('Callback must include both code and state query params.')
  }

  return { code, state }
}

async function openAuthUrl(url: string) {
  try {
    await openUrl(url)
    return
  } catch {
    const opened = window.open(url, '_blank', 'noopener,noreferrer')
    if (!opened) {
      throw new Error('Unable to open browser automatically. Copy and open the URL manually.')
    }
  }
}

export const useAuthStore = create<AuthState>((set, get) => ({
  status: null,
  pendingSessionId: readPendingSession(),
  pendingAuthUrl: null,
  loading: false,
  error: null,

  refreshStatus: async () => {
    set({ loading: true, error: null })
    try {
      const status = await getConfigStatus()
      set({ status, loading: false })
    } catch (error) {
      set({
        loading: false,
        error: error instanceof Error ? error.message : 'Failed to fetch auth status.',
      })
    }
  },

  beginAuth: async () => {
    set({ loading: true, error: null })
    try {
      const payload = await startAuth()
      await openAuthUrl(payload.authorize_url)
      writePendingSession(payload.session_id)
      set({
        pendingSessionId: payload.session_id,
        pendingAuthUrl: payload.authorize_url,
        loading: false,
      })
    } catch (error) {
      set({
        loading: false,
        error: error instanceof Error ? error.message : 'Failed to start auth.',
      })
    }
  },

  completeAuth: async (callbackUrl: string) => {
    const sessionId = get().pendingSessionId ?? readPendingSession()
    if (!sessionId) {
      set({ error: 'No active auth session. Start login first.' })
      return
    }

    set({ loading: true, error: null })
    try {
      const { code, state } = parseCallbackUrl(callbackUrl)
      await exchangeAuth(sessionId, state, code)
      await get().refreshStatus()
      writePendingSession(null)
      set({ pendingSessionId: null, pendingAuthUrl: null, loading: false })
    } catch (error) {
      set({
        loading: false,
        error: error instanceof Error ? error.message : 'Failed to complete auth.',
      })
    }
  },

  initDeepLinkListener: async () => {
    if (deepLinkListenerReady) {
      return
    }
    deepLinkListenerReady = true

    const existingToken = readCloudBearerToken()
    if (existingToken) {
      applyCloudTokenFallback(existingToken)
    }

    try {
      const { getCurrent, onOpenUrl } = await import('@tauri-apps/plugin-deep-link')

      const handle = async (url: string) => {
        if (url.startsWith('aftertaste://clerk-callback')) {
          const parsed = new URL(url)
          const token = parsed.searchParams.get('token')
          if (token) {
            writeCloudBearerToken(token)
            applyCloudTokenFallback(token)
            set({ error: null })
          }
          return
        }

        try {
          parseCallbackUrl(url)
        } catch {
          return
        }
        await get().completeAuth(url)
      }

      const current = await getCurrent()
      if (Array.isArray(current)) {
        for (const url of current) {
          void handle(url)
        }
      }

      await onOpenUrl((urls) => {
        for (const url of urls) {
          void handle(url)
        }
      })
    } catch {
      // Web mode or plugin unavailable.
    }
  },
}))
