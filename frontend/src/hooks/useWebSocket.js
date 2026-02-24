import { useEffect, useRef, useCallback, useState } from 'react'

export function useWebSocket(onMessage) {
  const socketRef = useRef(null)
  const [isConnected, setIsConnected] = useState(false)
  const reconnectTimeoutRef = useRef(null)
  const onMessageRef = useRef(onMessage)
  onMessageRef.current = onMessage

  const connect = useCallback(() => {
    const wsUrl = `ws://${window.location.hostname}:8000/ws`
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
      reconnectTimeoutRef.current = setTimeout(connect, 3000)
    }

    socket.onerror = () => {
      socket.close()
    }
  }, [])

  useEffect(() => {
    connect()
    const pingInterval = setInterval(() => {
      if (socketRef.current?.readyState === WebSocket.OPEN) {
        socketRef.current.send(JSON.stringify({ action: 'ping' }))
      }
    }, 20000)

    return () => {
      clearInterval(pingInterval)
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current)
      socketRef.current?.close()
    }
  }, [connect])

  return { isConnected }
}
