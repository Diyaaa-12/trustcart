import { useEffect, useState } from 'react';
import { Shield, ShieldCheck, History, Activity } from 'lucide-react';
import type { Cart, Product } from './types';
import { createCart, fetchCart, fetchCatalog } from './api/client';
import CartView from './components/CartView';
import { ProposalPanel } from './components/ProposalPanel';
import CheckoutButton from './components/CheckoutButton';
import { AuditReplayModal } from './components/AuditReplayModal';
import './index.css';

const SESSION_KEY = 'trustcart_session_id';

export default function App() {
  const [sessionId, setSessionId] = useState<string>('');
  const [cart, setCart] = useState<Cart | null>(null);
  const [catalog, setCatalog] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [initError, setInitError] = useState<string | null>(null);
  const [showReplay, setShowReplay] = useState(false);

  // Initialize session + catalog
  useEffect(() => {
    const init = async () => {
      setLoading(true);
      try {
        // Load catalog
        const products = await fetchCatalog();
        setCatalog(products);

        // Restore or create cart session
        let sid = localStorage.getItem(SESSION_KEY);
        if (sid) {
          try {
            const existingCart = await fetchCart(sid);
            setCart(existingCart);
            setSessionId(sid);
          } catch {
            sid = null;
          }
        }
        if (!sid) {
          const newCart = await createCart();
          localStorage.setItem(SESSION_KEY, newCart.session_id);
          setCart(newCart);
          setSessionId(newCart.session_id);
        }
      } catch (err) {
        console.error('Init failed:', err);
        setInitError('Unable to connect to backend. Make sure Docker Compose is running.');
      } finally {
        setLoading(false);
      }
    };
    void init();
  }, []);

  const handleNewSession = async () => {
    const newCart = await createCart();
    localStorage.setItem(SESSION_KEY, newCart.session_id);
    setCart(newCart);
    setSessionId(newCart.session_id);
  };

  const handleRefreshCart = async () => {
    if (!sessionId) return;
    try {
      const updated = await fetchCart(sessionId);
      setCart(updated);
    } catch (err) {
      console.error('Failed to refresh cart:', err);
    }
  };

  if (loading) {
    return (
      <div style={{
        display: 'flex', flexDirection: 'column', alignItems: 'center',
        justifyContent: 'center', height: '100vh', gap: '1rem',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <div className="spinner" style={{ width: 28, height: 28 }} />
          <span className="text-lg font-semi text-secondary">Loading TrustCart Merchant Portal...</span>
        </div>
      </div>
    );
  }

  if (initError) {
    return (
      <div style={{
        display: 'flex', flexDirection: 'column', alignItems: 'center',
        justifyContent: 'center', height: '100vh', gap: '1rem', padding: '2rem',
        textAlign: 'center',
      }}>
        <div className="text-xl font-semi" style={{ color: 'var(--danger)' }}>Connection Failed</div>
        <div className="text-sm text-secondary">{initError}</div>
        <pre style={{
          padding: '0.75rem 1.25rem', borderRadius: 8,
          background: 'var(--bg-card)', border: '1px solid var(--border)',
          fontSize: '0.8rem', color: 'var(--text-secondary)',
        }}>docker-compose up</pre>
      </div>
    );
  }

  const trustScore = Number(cart?.trust_score ?? 100);
  const autonomyTier = cart?.autonomy_tier ?? 'high';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: '100vh' }}>

      {/* Header */}
      <header style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '0.875rem 2rem',
        background: 'var(--bg-surface)',
        borderBottom: '1px solid var(--border)',
        position: 'sticky', top: 0, zIndex: 100,
      }}>
        {/* Logo */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <div style={{
            width: 34, height: 34, borderRadius: 8,
            background: 'var(--accent)',
            color: '#0F1420',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            <Shield size={20} strokeWidth={2.5} />
          </div>
          <div>
            <div className="text-base font-bold" style={{ color: 'var(--text-primary)', letterSpacing: '-0.01em' }}>
              TrustCart
            </div>
            <div className="text-xs text-muted" style={{ lineHeight: 1 }}>Auditable Merchant Commerce</div>
          </div>
        </div>

          {/* Right side -- Standardized Status Indicators */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.625rem' }}>
          {/* Trust Score & Autonomy Tier Indicator */}
          <div
            id="trust-score-indicator"
            className="status-indicator"
            style={{ cursor: 'pointer' }}
            onClick={() => setShowReplay(true)}
            title={`Trust Score: ${Number(trustScore).toFixed(0)}/100 (Tier: ${autonomyTier.toUpperCase()}) \u2014 Click to open Replay`}
          >
            <Activity
              size={14}
              style={{
                color: trustScore >= 70 ? 'var(--success)' : trustScore >= 40 ? 'var(--warning)' : 'var(--danger)',
              }}
            />
            <span>Trust: <strong style={{ color: 'var(--text-primary)' }}>{Number(trustScore).toFixed(0)}</strong></span>
            <span
              className="badge"
              style={{
                background:
                  trustScore >= 70 ? 'var(--success-bg)' : trustScore >= 40 ? 'var(--warning-bg)' : 'var(--danger-bg)',
                color:
                  trustScore >= 70 ? 'var(--success)' : trustScore >= 40 ? 'var(--warning)' : 'var(--danger)',
                border:
                  trustScore >= 70 ? '1px solid var(--success-border)' : trustScore >= 40 ? '1px solid var(--warning-border)' : '1px solid var(--danger-border)',
                fontSize: '0.65rem',
              }}
            >
              {autonomyTier}
            </span>
          </div>

          {/* Policy Gate Status Indicator */}
          <div className="status-indicator">
            <ShieldCheck size={14} style={{ color: 'var(--success)' }} />
            <span style={{ color: 'var(--success)', fontWeight: 600 }}>Policy Gate Active</span>
          </div>

          {/* Audit Replay Button */}
          <button
            id="open-replay-btn"
            className="btn btn-secondary btn-sm"
            style={{ height: 32, fontSize: '0.75rem', gap: 6 }}
            onClick={() => setShowReplay(true)}
            title="Open session timeline replay"
          >
            <History size={14} />
            <span>Audit Replay</span>
          </button>

          {/* Session ID Pill */}
          <div className="status-indicator" style={{ color: 'var(--text-muted)' }}>
            <span>Session:</span>
            <code style={{ color: 'var(--text-secondary)', fontSize: '0.72rem' }}>{sessionId.slice(0, 8)}</code>
          </div>

          <button className="btn btn-ghost btn-sm" style={{ height: 32 }} onClick={handleNewSession}>
            New Cart
          </button>
        </div>
      </header>

      {/* Sub-header Banner */}
      <div style={{
        background: '#131A2B',
        borderBottom: '1px solid var(--border)',
        padding: '0.45rem 2rem',
        display: 'flex', alignItems: 'center', gap: '0.65rem',
        fontSize: '0.75rem', color: 'var(--text-muted)',
      }}>
        <span style={{ color: 'var(--accent)', fontWeight: 600 }}>Razorpay AI Buildathon</span>
        <span>{"\u00B7"}</span>
        <span>Agentic Commerce Track</span>
        <span>{"\u00B7"}</span>
        <span>Bounded Autonomy {"\u00B7"} Trust Score {"\u00B7"} Full Auditability</span>
      </div>

      {/* Main Layout */}
      <main style={{
        flex: 1,
        display: 'grid',
        gridTemplateColumns: '1fr 420px',
        gap: '1.5rem',
        padding: '1.5rem 2rem',
        maxWidth: 1400,
        width: '100%',
        margin: '0 auto',
        alignItems: 'start',
      }}>
        {/* Left: Cart + Catalog */}
        <CartView
          cart={cart}
          catalog={catalog}
          sessionId={sessionId}
          onCartUpdate={setCart}
        />

        {/* Right: Proposal Panel + Settlement */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', position: 'sticky', top: 76 }}>
          <ProposalPanel
            sessionId={sessionId}
            onCartUpdate={setCart}
            onRefreshCart={handleRefreshCart}
          />
          {/* Most prominent card: Checkout & Settlement */}
          <div className="card card-settlement" style={{ padding: '1.25rem' }}>
            <CheckoutButton cart={cart} sessionId={sessionId} />
          </div>
        </div>
      </main>

      {/* Footer */}
      <footer style={{
        borderTop: '1px solid var(--border)',
        padding: '1rem 2rem',
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        fontSize: '0.75rem', color: 'var(--text-muted)',
      }}>
        <span>TrustCart {"\u00B7"} Phase 2 {"\u00B7"} Razorpay AI Buildathon 2026</span>
        <div style={{ display: 'flex', gap: '1rem' }}>
          <a href="/api/docs" target="_blank" rel="noreferrer" style={{ color: 'var(--text-muted)', textDecoration: 'none' }}>
            API Docs
          </a>
          <a href={`/api/audit/${sessionId}`} target="_blank" rel="noreferrer" style={{ color: 'var(--text-muted)', textDecoration: 'none' }}>
            Audit Log
          </a>
          <button
            onClick={() => setShowReplay(true)}
            style={{ background: 'none', border: 'none', color: 'var(--accent)', cursor: 'pointer', fontSize: '0.75rem', padding: 0 }}
          >
            Replay Session
          </button>
        </div>
      </footer>

      {/* Audit Replay Modal */}
      <AuditReplayModal
        sessionId={sessionId}
        isOpen={showReplay}
        onClose={() => setShowReplay(false)}
      />
    </div>
  );
}
