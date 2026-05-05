export const clerkPublishableKey = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY as string | undefined
export const clerkJwtTemplate = import.meta.env.VITE_CLERK_JWT_TEMPLATE as string | undefined
export const cloudSignInBaseUrl = import.meta.env.VITE_CLOUD_SIGNIN_BASE_URL as string | undefined

export function isClerkEnabled() {
  return Boolean(clerkPublishableKey)
}
