import { type ReactNode, useEffect } from 'react'

import {
  ClerkLoaded,
  ClerkLoading,
  ClerkProvider,
  SignIn,
  SignedIn,
  SignedOut,
  UserButton,
  useAuth,
} from '@clerk/clerk-react'

import { setAuthTokenProvider } from '../api/client'

const publishableKey = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY as string | undefined

export function isClerkEnabled() {
  return Boolean(publishableKey)
}

export function MaybeClerkProvider({ children }: { children: ReactNode }) {
  if (!publishableKey) {
    return <>{children}</>
  }

  return (
    <ClerkProvider publishableKey={publishableKey}>
      {children}
    </ClerkProvider>
  )
}

function ClerkTokenBridge() {
  const { getToken } = useAuth()

  useEffect(() => {
    setAuthTokenProvider(async () => (await getToken()) ?? null)
    return () => {
      setAuthTokenProvider(null)
    }
  }, [getToken])

  return null
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
        <SignedIn>
          <ClerkTokenBridge />
          {children}
        </SignedIn>
        <SignedOut>
          <main className="screen">
            <section className="panel auth-panel">
              <h3>Sign in to Aftertaste Cloud</h3>
              <SignIn
                routing="hash"
                fallbackRedirectUrl="/"
                forceRedirectUrl="/"
              />
            </section>
          </main>
        </SignedOut>
      </ClerkLoaded>
    </>
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
