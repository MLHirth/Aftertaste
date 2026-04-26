import { create } from 'zustand'

import { generateToday, generateVibeRevival, getTodayMix } from '../api/client'
import type { MixTrack } from '../types'

type ScoringState = {
  tracks: MixTrack[]
  loading: boolean
  generating: boolean
  error: string | null
  refreshMix: () => Promise<void>
  generateMix: (writeToSpotify: boolean) => Promise<void>
  generateVibeRevivalMix: (writeToSpotify: boolean) => Promise<void>
}

export const useScoringStore = create<ScoringState>((set, get) => ({
  tracks: [],
  loading: false,
  generating: false,
  error: null,

  refreshMix: async () => {
    set({ loading: true, error: null })
    try {
      const tracks = await getTodayMix()
      set({ tracks, loading: false })
    } catch (error) {
      set({
        loading: false,
        error: error instanceof Error ? error.message : 'Failed to load today mix.',
      })
    }
  },

  generateMix: async (writeToSpotify: boolean) => {
    set({ generating: true, error: null })
    try {
      const payload = await generateToday(writeToSpotify)
      set({ tracks: payload.tracks, generating: false })
    } catch (error) {
      set({
        generating: false,
        error: error instanceof Error ? error.message : 'Failed to generate mix.',
      })
    }
    await get().refreshMix()
  },

  generateVibeRevivalMix: async (writeToSpotify: boolean) => {
    set({ generating: true, error: null })
    try {
      const payload = await generateVibeRevival(writeToSpotify)
      set({ tracks: payload.tracks, generating: false })
    } catch (error) {
      set({
        generating: false,
        error: error instanceof Error ? error.message : 'Failed to generate vibe revival mix.',
      })
    }
  },
}))
