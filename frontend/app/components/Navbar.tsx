'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

const NAV_ITEMS = [
    { href: '/', label: '🏠 Dashboard', icon: '⚡' },
    { href: '/leaderboard', label: '🏆 Leaderboard', icon: '📊' },
    { href: '/metagame', label: '🎯 Metagame', icon: '🌐' },
    { href: '/gauntlet', label: '⏳ Time Machine', icon: '🕰️' },
    { href: '/engine', label: '⚙️ Engine Room', icon: '🔧' },
    { href: '/heatmaps', label: '🔥 Heatmaps', icon: '📈' },
    { href: '/hall-of-fame', label: '🏛️ Hall of Fame', icon: '👑' },
    { href: '/salt-score', label: '🧂 Salt Score', icon: '⚠️' },
];

export default function Navbar() {
    const pathname = usePathname();

    return (
        <nav style={{
            background: 'var(--bg-secondary)',
            borderBottom: '1px solid var(--border)',
            padding: '0 2rem',
            position: 'sticky',
            top: 0,
            zIndex: 100,
            backdropFilter: 'blur(12px)',
        }}>
            <div style={{
                maxWidth: '1400px',
                margin: '0 auto',
                display: 'flex',
                alignItems: 'center',
                height: '60px',
                gap: '0.5rem',
            }}>
                <Link href="/" style={{
                    fontWeight: 800,
                    fontSize: '1.1rem',
                    background: 'linear-gradient(135deg, var(--accent-gold), var(--accent-emerald))',
                    WebkitBackgroundClip: 'text',
                    WebkitTextFillColor: 'transparent',
                    textDecoration: 'none',
                    marginRight: '2rem',
                    whiteSpace: 'nowrap',
                }}>
                    🧬 MTG Genetic League
                </Link>

                {NAV_ITEMS.map(({ href, label }) => {
                    const isActive = pathname === href;
                    return (
                        <Link
                            key={href}
                            href={href}
                            style={{
                                color: isActive ? 'var(--accent-gold)' : 'var(--text-secondary)',
                                textDecoration: 'none',
                                fontSize: '0.85rem',
                                fontWeight: isActive ? 600 : 400,
                                padding: '0.4rem 0.75rem',
                                borderRadius: 'var(--radius-sm)',
                                background: isActive ? 'rgba(240, 180, 41, 0.1)' : 'transparent',
                                transition: 'all 0.2s',
                                whiteSpace: 'nowrap',
                            }}
                        >
                            {label}
                        </Link>
                    );
                })}
            </div>
        </nav>
    );
}
