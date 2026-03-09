'use client';

import { useEffect, useState } from 'react';
import { apiFetch, apiPost } from '../lib/api';

interface EngineConfigData {
    max_workers: number;
    cpu_count: number;
    memory_limit_mb: number;
    headless_mode: boolean;
}

export default function EnginePage() {
    const [config, setConfig] = useState<EngineConfigData | null>(null);
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);

    useEffect(() => {
        apiFetch<EngineConfigData>('/api/engine/config').then(setConfig);
    }, []);

    async function saveConfig() {
        if (!config) return;
        setSaving(true);
        try {
            const res = await apiPost<{ config: EngineConfigData }>('/api/engine/config', config);
            setConfig(res.config);
            setSaved(true);
            setTimeout(() => setSaved(false), 2000);
        } catch (err: any) {
            console.error('Failed to save config:', err);
            alert(`Failed to save config: ${err.message || 'Unknown error'}`);
        } finally {
            setSaving(false);
        }
    }

    if (!config) return <div className="page-container"><p style={{ color: 'var(--text-secondary)' }}>Loading...</p></div>;

    return (
        <div className="page-container">
            <h1 className="page-title">⚙️ Engine Room</h1>
            <p className="page-subtitle">Control simulation resources in real-time</p>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '1.5rem', marginBottom: '2rem' }}>
                {/* Thread Count */}
                <div className="card">
                    <h3 className="card-title">🧵 Thread Count</h3>
                    <p style={{ color: 'var(--text-muted)', fontSize: '0.8rem', margin: '0.5rem 0 1rem' }}>
                        Parallel worker processes (1 – {config.cpu_count} cores)
                    </p>
                    <div className="slider-container">
                        <input
                            type="range"
                            className="slider"
                            min={1}
                            max={config.cpu_count}
                            value={config.max_workers}
                            onChange={(e) => setConfig({ ...config, max_workers: parseInt(e.target.value) })}
                        />
                        <span className="slider-value">{config.max_workers}</span>
                    </div>
                    <div style={{
                        marginTop: '1rem',
                        padding: '0.75rem',
                        background: 'var(--bg-secondary)',
                        borderRadius: 'var(--radius-sm)',
                        textAlign: 'center',
                    }}>
                        <span style={{ fontFamily: 'var(--font-mono)', fontSize: '1.5rem', fontWeight: 800, color: 'var(--accent-gold)' }}>
                            {config.max_workers}
                        </span>
                        <span style={{ color: 'var(--text-muted)', fontSize: '0.8rem', marginLeft: 8 }}>
                            / {config.cpu_count} cores
                        </span>
                    </div>
                </div>

                {/* Memory Cap */}
                <div className="card">
                    <h3 className="card-title">💾 Memory Cap</h3>
                    <p style={{ color: 'var(--text-muted)', fontSize: '0.8rem', margin: '0.5rem 0 1rem' }}>
                        Soft heap limit per worker (MB). 0 = unlimited.
                    </p>
                    <input
                        type="number"
                        className="input"
                        value={config.memory_limit_mb}
                        onChange={(e) => setConfig({ ...config, memory_limit_mb: Math.max(0, parseInt(e.target.value) || 0) })}
                        min={0}
                        step={64}
                        style={{ textAlign: 'center', fontFamily: 'var(--font-mono)', fontSize: '1.2rem' }}
                    />
                    <div style={{ marginTop: '0.75rem', display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
                        {[0, 256, 512, 1024, 2048].map(v => (
                            <button
                                key={v}
                                className="btn btn-secondary"
                                style={{ fontSize: '0.75rem', padding: '0.3rem 0.6rem' }}
                                onClick={() => setConfig({ ...config, memory_limit_mb: v })}
                            >
                                {v === 0 ? '∞' : `${v}MB`}
                            </button>
                        ))}
                    </div>
                </div>

                {/* Concurrency Mode */}
                <div className="card">
                    <h3 className="card-title">🎯 Concurrency Mode</h3>
                    <p style={{ color: 'var(--text-muted)', fontSize: '0.8rem', margin: '0.5rem 0 1rem' }}>
                        Toggle between headless (max speed) and visualizer (verbose logging).
                    </p>
                    <div style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        gap: '1rem',
                        marginTop: '1.5rem',
                    }}>
                        <span style={{
                            fontWeight: !config.headless_mode ? 700 : 400,
                            color: !config.headless_mode ? 'var(--accent-emerald)' : 'var(--text-muted)',
                        }}>
                            👁️ Visualizer
                        </span>
                        <div
                            className={`toggle ${config.headless_mode ? 'active' : ''}`}
                            onClick={() => setConfig({ ...config, headless_mode: !config.headless_mode })}
                        />
                        <span style={{
                            fontWeight: config.headless_mode ? 700 : 400,
                            color: config.headless_mode ? 'var(--accent-gold)' : 'var(--text-muted)',
                        }}>
                            🚀 Headless
                        </span>
                    </div>
                    <div style={{
                        marginTop: '1rem',
                        textAlign: 'center',
                        fontSize: '0.8rem',
                        color: 'var(--text-muted)',
                    }}>
                        {config.headless_mode ? 'Maximum simulation speed — minimal logging' : 'Verbose board state logging — slower but visible'}
                    </div>
                </div>
            </div>

            <button
                className="btn btn-primary"
                onClick={saveConfig}
                disabled={saving}
                style={{ minWidth: 200 }}
            >
                {saving ? '⏳ Saving...' : saved ? '✅ Saved!' : '💾 Apply Changes'}
            </button>
        </div>
    );
}
