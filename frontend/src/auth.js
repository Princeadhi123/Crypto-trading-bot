/**
 * Auth helpers — JWT token management for the login system.
 * JWT is stored in sessionStorage (cleared when browser tab closes).
 */

const _SESSION_KEY = 'cryptobot_jwt'

/** Returns the active auth token: JWT from sessionStorage */
export function getAuthToken() {
  return sessionStorage.getItem(_SESSION_KEY)
}

/** Store a JWT received from /api/auth/login */
export function setAuthToken(token) {
  sessionStorage.setItem(_SESSION_KEY, token)
}

/** Clear stored JWT (logout) */
export function clearAuthToken() {
  sessionStorage.removeItem(_SESSION_KEY)
}

/** True if user has a valid non-expired JWT */
export function isAuthenticated() {
  const token = sessionStorage.getItem(_SESSION_KEY)
  if (!token) return false
  try {
    const payload = JSON.parse(atob(token.split('.')[1]))
    return payload.exp * 1000 > Date.now()
  } catch {
    return false
  }
}

/** True when the login system should be shown */
export function needsLogin() {
  return !isAuthenticated()
}
