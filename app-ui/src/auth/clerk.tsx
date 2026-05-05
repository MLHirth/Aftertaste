import { type ReactNode, useCallback, useEffect, useMemo, useState } from 'react'

import { ClerkLoaded, ClerkLoading, ClerkProvider, SignIn, SignedIn, UserButton, useAuth } from '@clerk/clerk-react'

import { setAuthRequired, setAuthTokenProvider } from '../api/client'
import { useAuthStore } from '../state/authStore'
import {
  clerkJwtTemplate,
  clerkPublishableKey,
  cloudSignInBaseUrl,
  isClerkEnabled,
} from './config'

setAuthRequired(isClerkEnabled())

const clerkAppearance = {
  variables: {
    colorPrimary: '#1f5543',
    colorText: '#211e1a',
    colorTextSecondary: '#4d463d',
    colorBackground: '#ffffff',
    colorInputBackground: '#ffffff',
    colorInputText: '#211e1a',
    colorDanger: '#944c4c',
    borderRadius: '0.6rem',
  },
}

function isTauriDesktop() {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
}

function buildDesktopBridgeUrl() {
  if (!cloudSignInBaseUrl) {
    return null
  }
  try {
    const url = new URL(cloudSignInBaseUrl)
    url.hash = '/desktop-auth'
    return url.toString()
  } catch {
    return null
  }
}

async function openExternal(url: string) {
  try {
    const { openUrl } = await import('@tauri-apps/plugin-opener')
    await openUrl(url)
    return
  } catch {
    const opened = window.open(url, '_blank', 'noopener,noreferrer')
    if (!opened) {
      throw new Error('Unable to open browser automatically.')
    }
  }
}

export function MaybeClerkProvider({ children }: { children: ReactNode }) {
  if (!clerkPublishableKey) {
    return <>{children}</>
  }

  return (
    <ClerkProvider publishableKey={clerkPublishableKey} appearance={clerkAppearance}>
      {children}
    </ClerkProvider>
  )
}

function LoadingAccount() {
  return (
    <main className="screen">
      <section className="panel">
        <h3>Loading account...</h3>
      </section>
    </main>
  )
}

function ClerkTokenBridge({ onReady }: { onReady: () => void }) {
  const { getToken } = useAuth()

  useEffect(() => {
    setAuthTokenProvider(
      async () =>
        (await getToken(
          clerkJwtTemplate
            ? { template: clerkJwtTemplate }
            : undefined,
        )) ?? null,
    )
    onReady()
    return () => {
      setAuthTokenProvider(null)
    }
  }, [getToken, onReady])

  return null
}

function DesktopSignInOutOfApp() {
  const auth = useAuthStore()
  const [error, setError] = useState<string | null>(null)
  const [handoffUrl, setHandoffUrl] = useState('')
  const bridgeUrl = useMemo(() => buildDesktopBridgeUrl(), [])

  return (
    <main className="screen">
      <section className="panel auth-panel">
        <h3>Sign in from Browser</h3>
        <p>
          Continue in your browser, then use <code>Open in app</code> from the web bridge to
          return here.
        </p>
        <div className="row-actions">
          <button
            onClick={() => {
              if (!bridgeUrl) {
                setError(
                  'Missing VITE_CLOUD_SIGNIN_BASE_URL. Set it to your hosted app URL and restart.',
                )
                return
              }
              void openExternal(bridgeUrl).catch((reason: unknown) => {
                setError(reason instanceof Error ? reason.message : 'Failed to open browser.')
              })
            }}
          >
            Sign In In Browser
          </button>
        </div>

        {bridgeUrl && (
          <label className="input-group">
            Browser sign-in URL
            <textarea readOnly rows={2} value={bridgeUrl} />
          </label>
        )}
        <label className="input-group">
          Fallback: paste desktop handoff URL
          <textarea
            rows={2}
            placeholder="aftertaste://clerk-callback?token=..."
            value={handoffUrl}
            onChange={(event) => setHandoffUrl(event.target.value)}
          />
        </label>
        <div className="row-actions">
          <button
            className="button-secondary"
            onClick={() => {
              void auth.acceptClerkHandoff(handoffUrl)
            }}
            disabled={!handoffUrl.trim()}
          >
            Apply Browser Handoff
          </button>
        </div>
        {error && <p className="muted">{error}</p>}
        {auth.error && <p className="muted">{auth.error}</p>}
      </section>
    </main>
  )
}

export function AuthGate({ children }: { children: ReactNode }) {
  if (!clerkPublishableKey) {
    return <>{children}</>
  }

  return (
    <>
      <ClerkLoading>
        <LoadingAccount />
      </ClerkLoading>
      <ClerkLoaded>
        <AuthGateLoaded>{children}</AuthGateLoaded>
      </ClerkLoaded>
    </>
  )
}

function AuthGateLoaded({ children }: { children: ReactNode }) {
  const { isSignedIn } = useAuth()
  const hasCloudBearerToken = useAuthStore((state) => state.hasCloudBearerToken)
  const desktopCloudSession = isTauriDesktop() && hasCloudBearerToken

  if (isSignedIn) {
    return <SignedInAuthGate>{children}</SignedInAuthGate>
  }

  if (desktopCloudSession) {
    return <>{children}</>
  }

  if (isTauriDesktop()) {
    return <DesktopSignInOutOfApp />
  }

  return (
    <main className="screen">
      <section className="panel auth-panel">
        <h3>Sign in to Aftertaste Cloud</h3>
        <SignIn routing="hash" fallbackRedirectUrl="/desktop-auth" forceRedirectUrl="/desktop-auth" />
      </section>
    </main>
  )
}

function SignedInAuthGate({ children }: { children: ReactNode }) {
  const [tokenBridgeReady, setTokenBridgeReady] = useState(false)
  const markTokenBridgeReady = useCallback(() => setTokenBridgeReady(true), [])

  return (
    <>
      <ClerkTokenBridge onReady={markTokenBridgeReady} />
      {tokenBridgeReady ? children : <LoadingAccount />}
    </>
  )
}

export function AccountControl() {
  if (!clerkPublishableKey) {
    return null
  }

  return (
    <SignedIn>
      <div className="account-chip">
        <UserButton afterSignOutUrl="/" />
      </div>
    </SignedIn>
  )
}

function SessionStatusNoClerk() {
  const status = useAuthStore((state) => state.status)
  const spotifyLabel = isTauriDesktop()
    ? `Spotify: ${status?.authorized ? 'connected' : 'not connected'}`
    : `Spotify: server-managed ${status?.cloud_spotify_connected ? 'connected' : 'not connected'}`

  return (
    <div className="session-chip">
      <span className={status?.authorized ? 'ok' : ''}>{spotifyLabel}</span>
      <span>Cloud: Clerk disabled</span>
    </div>
  )
}

function SessionStatusWithClerk() {
  const { isSignedIn, userId } = useAuth()
  const status = useAuthStore((state) => state.status)
  const hasCloudBearerToken = useAuthStore((state) => state.hasCloudBearerToken)
  const cloudIdentity = useAuthStore((state) => state.cloudIdentity)
  const spotifyLabel = isTauriDesktop()
    ? `Spotify: ${status?.authorized ? 'connected' : 'not connected'}`
    : `Spotify token: ${status?.spotify_refresh_token_present ? 'present' : 'missing'}`
  const probeLabel =
    status?.spotify_live_probe_ok === undefined || status?.spotify_live_probe_ok === null
      ? 'Spotify probe: pending'
      : `Spotify probe: ${status.spotify_live_probe_ok ? 'ok' : 'failed'}`
  const cloudApiLabel = `Cloud API: ${status?.cloud_api_auth_ok ? 'JWT ok' : 'pending'}`

  let cloudLabel = 'Cloud: not connected'
  if (isSignedIn) {
    cloudLabel = `Clerk: ${userId ?? 'signed in'}`
  } else if (hasCloudBearerToken) {
    cloudLabel = `Handoff: ${cloudIdentity ?? 'token loaded'}`
  }

  return (
    <div className="session-chip">
      <span className={isSignedIn || hasCloudBearerToken ? 'ok' : ''}>{cloudLabel}</span>
      <span className={status?.cloud_api_auth_ok ? 'ok' : ''}>{cloudApiLabel}</span>
      <span className={status?.spotify_refresh_token_present ? 'ok' : ''}>{spotifyLabel}</span>
      {!isTauriDesktop() && <span className={status?.spotify_live_probe_ok ? 'ok' : ''}>{probeLabel}</span>}
    </div>
  )
}

export function SessionStatus() {
  if (!clerkPublishableKey) {
    return <SessionStatusNoClerk />
  }
  return <SessionStatusWithClerk />
}
