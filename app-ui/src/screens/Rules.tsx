import { useEffect, useState } from 'react'

import { getRules, saveRules } from '../api/client'
import type { RuleSet } from '../types'

const orderedKeys = [
  'early_skip_cutoff_seconds',
  'completion_ratio_threshold',
  'mid_skip_ratio_threshold',
  'revival_days_30',
  'revival_days_90',
  'revival_days_180',
  'exploration_ratio',
  'max_same_artist_per_20',
  'recent_artist_random_enabled',
  'recent_artist_random_slots',
  'recent_artist_random_days',
]

function labelFor(key: string) {
  return key.replaceAll('_', ' ')
}

export function Rules() {
  const [rules, setRules] = useState<RuleSet>({})
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getRules()
      .then((loaded) => setRules(loaded))
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : 'Failed to load rules.')
      })
  }, [])

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      const saved = await saveRules(rules)
      setRules(saved)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Failed to save rules.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <main className="screen">
      <section className="hero-card">
        <div>
          <h2>Rules</h2>
          <p>Editable thresholds that directly shape score components and filtering behavior.</p>
        </div>
      </section>

      <section className="panel">
        <h3>Scoring Thresholds</h3>
        <p className="muted">
          Set <code>recent artist random enabled</code> to <code>1</code> to opt in, or
          <code>0</code> to disable.
        </p>
        <div className="rules-grid">
          {orderedKeys.map((key) => (
            <label key={key} className="input-group">
              {labelFor(key)}
              <input
                type="number"
                step="0.1"
                value={rules[key] ?? 0}
                onChange={(event) => {
                  const next = Number(event.target.value)
                  setRules((current) => ({
                    ...current,
                    [key]: Number.isFinite(next) ? next : 0,
                  }))
                }}
              />
            </label>
          ))}
        </div>
        <button onClick={() => void save()} disabled={saving}>
          {saving ? 'Saving...' : 'Save Rules'}
        </button>
      </section>

      {error && (
        <section className="panel error">
          <strong>Error</strong>
          <p>{error}</p>
        </section>
      )}
    </main>
  )
}
