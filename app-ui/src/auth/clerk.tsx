import { type ReactNode, useEffect, useMemo, useState } from 'react'

import { ClerkLoaded, ClerkLoading, ClerkProvider, SignIn, SignedIn, UserButton, useAuth } from '@clerk/clerk-react'

import { setAuthTokenProvider } from '../api/client'
import { useAuthStore } from '../state/authStore'

const publishableKey = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY as string | undefined
const clerkJwtTemplate = import.meta.env.VITE_CLERK_JWT_TEMPLATE as string | undefined
const cloudSignInBaseUrl = import.meta.env.VITE_CLOUD_SIGNIN_BASE_URL as string | undefined

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

export function isClerkEnabled() {
  return Boolean(publishableKey)
}

export function MaybeClerkProvider({ children }: { children: ReactNode }) {
  if (!publishableKey) {
    return <>{children}</>
  }

  return (
    <ClerkProvider publishableKey={publishableKey} appearance={clerkAppearance}>
      {children}
    </ClerkProvider>
  )
}

function ClerkTokenBridge() {
  const { getToken } = useAuth()

  useEffect(() => {
    setAuthTokenProvider(
      async () =>
        (await getToken(clerkJwtTemplate ? { template: clerkJwtTemplate } : undefined)) ??
        null,
    )
    return () => {
      setAuthTokenProvider(null)
    }
  }, [getToken])

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
  if (!publishableKey) {
    return <>{children}</>
  }

  return (
    <>
      <ClerkLoading>
        <main className="screen">
          <section className="panel">
            <h3>Loading account...</h3>
          </section>
        </main>
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
    return (
      <>
        <ClerkTokenBridge />
        {children}
      </>
    )
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

export function AccountControl() {
  if (!publishableKey) {
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
