import { create } from 'zustand'

import { getDashboard, startPoller, stopPoller, syncAll } from '../api/client'
import type { DashboardData } from '../types'

type PlaybackState = {
  dashboard: DashboardData | null
  loading: boolean
  syncing: boolean
  error: string | null
  refreshDashboard: () => Promise<void>
  runSync: () => Promise<void>
  setPoller: (running: boolean) => Promise<void>
}

export const usePlaybackStore = create<PlaybackState>((set, get) => ({
  dashboard: null,
  loading: false,
  syncing: false,
  error: null,

  refreshDashboard: async () => {
    set({ loading: true, error: null })
    try {
      const dashboard = await getDashboard()
      set({ dashboard, loading: false })
    } catch (error) {
      set({
        loading: false,
        error: error instanceof Error ? error.message : 'Failed to load dashboard.',
      })
    }
  },

  runSync: async () => {
    set({ syncing: true, error: null })
    try {
      await syncAll()
      await get().refreshDashboard()
      set({ syncing: false })
    } catch (error) {
      set({
        syncing: false,
        error: error instanceof Error ? error.message : 'Sync failed.',
      })
    }
  },

  setPoller: async (running: boolean) => {
    set({ loading: true, error: null })
    try {
      if (running) {
        await startPoller()
      } else {
        await stopPoller()
      }
      await get().refreshDashboard()
      set({ loading: false })
    } catch (error) {
      set({
        loading: false,
        error: error instanceof Error ? error.message : 'Failed to update poller state.',
      })
    }
  },
}))
