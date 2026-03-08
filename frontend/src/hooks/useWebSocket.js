import { useEffect, useRef, useCallback, useState } from 'react'
import { getAuthToken } from '../auth'

export function useWebSocket(onMessage, enabled = true) {
  const socketRef = useRef(null)
  const [isConnected, setIsConnected] = useState(false)
  const reconnectTimeoutRef = useRef(null)
  const onMessageRef = useRef(onMessage)
  const isMountedRef = useRef(true)
  onMessageRef.current = onMessage

  const connect = useCallback(() => {
    if (!isMountedRef.current) return
    if (!enabled) return
    // Use the same host/protocol as the page — routes through Vite proxy in dev,
    // works correctly in any deployed environment without hardcoding a port.
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const baseUrl = `${protocol}//${window.location.host}/ws`
    const token = getAuthToken()
    if (!token) {
      setIsConnected(false)
      return
    }
    const wsUrl = token ? `${baseUrl}?token=${encodeURIComponent(token)}` : baseUrl
    const socket = new WebSocket(wsUrl)
    socketRef.current = socket

    socket.onopen = () => {
      setIsConnected(true)
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current)
      }
    }

    socket.onmessage = (event) => {
      try {
        const parsed = JSON.parse(event.data)
        onMessageRef.current(parsed)
      } catch (e) {
        // ignore malformed messages
      }
    }

    socket.onclose = () => {
      setIsConnected(false)
      // Only schedule reconnect if still mounted — prevents leak after unmount
      if (isMountedRef.current && enabled && getAuthToken()) {
        reconnectTimeoutRef.current = setTimeout(connect, 3000)
      }
    }

    socket.onerror = () => {
      socket.close()
    }
  }, [enabled])

  useEffect(() => {
    isMountedRef.current = true
    if (enabled) {
      connect()
    }
    const pingInterval = setInterval(() => {
      if (socketRef.current?.readyState === WebSocket.OPEN) {
        socketRef.current.send(JSON.stringify({ action: 'ping' }))
      }
    }, 20000)

    return () => {
      isMountedRef.current = false
      clearInterval(pingInterval)
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current)
      socketRef.current?.close()
    }
  }, [connect, enabled])

  return { isConnected }
}
