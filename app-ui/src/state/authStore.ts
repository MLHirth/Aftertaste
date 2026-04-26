import { create } from 'zustand'
import { openUrl } from '@tauri-apps/plugin-opener'

import { exchangeAuth, getConfigStatus, startAuth } from '../api/client'
import type { ConfigStatus } from '../types'

type AuthState = {
  status: ConfigStatus | null
  pendingSessionId: string | null
  pendingAuthUrl: string | null
  loading: boolean
  error: string | null
  refreshStatus: () => Promise<void>
  beginAuth: () => Promise<void>
  completeAuth: (callbackUrl: string) => Promise<void>
}

function parseCallbackUrl(input: string): { code: string; state: string } {
  const trimmed = input.trim()
  const url = trimmed.startsWith('http') ? new URL(trimmed) : new URL(`http://x?${trimmed}`)

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
  pendingSessionId: null,
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
    const sessionId = get().pendingSessionId
    if (!sessionId) {
      set({ error: 'No active auth session. Start login first.' })
      return
    }

    set({ loading: true, error: null })
    try {
      const { code, state } = parseCallbackUrl(callbackUrl)
      await exchangeAuth(sessionId, state, code)
      await get().refreshStatus()
      set({ pendingSessionId: null, pendingAuthUrl: null, loading: false })
    } catch (error) {
      set({
        loading: false,
        error: error instanceof Error ? error.message : 'Failed to complete auth.',
      })
    }
  },
}))
