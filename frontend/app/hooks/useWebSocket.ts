'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import { WS_BASE } from '@/app/lib/api';

interface EloUpdate {
    type: string;
    deck_id: number;
    deck_name: string;
    old_elo: number;
    new_elo: number;
    delta: number;
    match_id: number;
    timestamp: string;
}

interface UseWebSocketReturn {
    eloUpdates: EloUpdate[];
    isConnected: boolean;
    connectionError: string | null;
}

export function useEloStream(maxUpdates: number = 50): UseWebSocketReturn {
    const [eloUpdates, setEloUpdates] = useState<EloUpdate[]>([]);
    const [isConnected, setIsConnected] = useState(false);
    const [connectionError, setConnectionError] = useState<string | null>(null);
    const wsRef = useRef<WebSocket | null>(null);
    const reconnectTimeout = useRef<NodeJS.Timeout | null>(null);
    const reconnectAttempts = useRef(0);

    const connect = useCallback(() => {
        try {
            const ws = new WebSocket(`${WS_BASE}/ws/elo-stream`);
            wsRef.current = ws;

            ws.onopen = () => {
                setIsConnected(true);
                setConnectionError(null);
                reconnectAttempts.current = 0;
            };

            ws.onmessage = (event) => {
                try {
                    const data: EloUpdate = JSON.parse(event.data);
                    if (data.type === 'elo_update') {
                        data.delta = data.new_elo - data.old_elo;
                        setEloUpdates((prev) => [data, ...prev].slice(0, maxUpdates));
                    }
                } catch (e) {
                    // Ignore malformed messages
                }
            };

            ws.onclose = () => {
                setIsConnected(false);
                // Exponential backoff: 1s, 2s, 4s, 8s, max 30s
                const delay = Math.min(1000 * Math.pow(2, reconnectAttempts.current), 30000);
                reconnectAttempts.current += 1;
                reconnectTimeout.current = setTimeout(connect, delay);
            };

            ws.onerror = () => {
                setConnectionError('WebSocket connection failed');
                ws.close();
            };
        } catch (e) {
            setConnectionError('Failed to create WebSocket');
        }
    }, [maxUpdates]);

    useEffect(() => {
        connect();
        return () => {
            if (reconnectTimeout.current) clearTimeout(reconnectTimeout.current);
            if (wsRef.current) wsRef.current.close();
        };
    }, [connect]);

    return { eloUpdates, isConnected, connectionError };
}
