import { useAuth, SignedIn, SignedOut, SignIn } from '@clerk/clerk-react'
import { useEffect, useMemo, useState } from 'react'

const clerkJwtTemplate = import.meta.env.VITE_CLERK_JWT_TEMPLATE as string | undefined

function buildDeepLink(token: string) {
  return `aftertaste://clerk-callback?token=${encodeURIComponent(token)}`
}

function SignedInBridge() {
  const { getToken } = useAuth()
  const [deepLink, setDeepLink] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    void getToken(clerkJwtTemplate ? { template: clerkJwtTemplate } : undefined)
      .then((token) => {
        if (!token) {
          setError('No Clerk token returned for app handoff.')
          return
        }
        setDeepLink(buildDeepLink(token))
      })
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : 'Failed to generate app handoff token.')
      })
  }, [getToken])

  return (
    <main className="screen">
      <section className="panel auth-panel">
        <h3>Continue in Desktop App</h3>
        <p>
          You are signed in. Click <code>Open in app</code> to hand this session to Aftertaste
          desktop.
        </p>
        {deepLink && (
          <div className="row-actions">
            <button
              onClick={() => {
                window.location.href = deepLink
              }}
            >
              Open in app
            </button>
          </div>
        )}
        {deepLink && (
          <label className="input-group">
            Desktop deep-link
            <textarea readOnly value={deepLink} rows={3} />
          </label>
        )}
        {error && <p className="muted">{error}</p>}
      </section>
    </main>
  )
}

export function DesktopAuthBridge() {
  const redirect = useMemo(() => '/desktop-auth', [])

  return (
    <>
      <SignedIn>
        <SignedInBridge />
      </SignedIn>
      <SignedOut>
        <main className="screen">
          <section className="panel auth-panel">
            <h3>Sign in to continue</h3>
            <SignIn routing="hash" fallbackRedirectUrl={redirect} forceRedirectUrl={redirect} />
          </section>
        </main>
      </SignedOut>
    </>
  )
}
