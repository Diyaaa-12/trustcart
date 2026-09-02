import { useEffect, useState } from 'react';
import { Zap, ShieldCheck, History } from 'lucide-react';
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
          <span className="text-lg font-semi text-secondary">Loading TrustCart...</span>
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

  const trustScore = cart?.trust_score ?? 100;
  const autonomyTier = cart?.autonomy_tier ?? 'high';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: '100vh' }}>

      {/* Header */}
      <header style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '1rem 2rem',
        background: 'rgba(17,20,32,0.85)',
        backdropFilter: 'blur(12px)',
        borderBottom: '1px solid var(--border)',
        position: 'sticky', top: 0, zIndex: 100,
      }}>
        {/* Logo */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <div style={{
            width: 36, height: 36, borderRadius: 10,
            background: 'linear-gradient(135deg, var(--accent), #818cf8)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            boxShadow: '0 0 20px var(--accent-glow)',
          }}>
            <Zap size={18} color="#fff" fill="#fff" />
          </div>
          <div>
            <div className="text-lg font-bold" style={{
              background: 'linear-gradient(135deg, #e2e8f0, var(--accent-light))',
              WebkitBackgroundClip: 'text',
              WebkitTextFillColor: 'transparent',
            }}>
              TrustCart
            </div>
            <div className="text-xs text-muted" style={{ lineHeight: 1 }}>Auditable AI Commerce</div>
          </div>
        </div>

        {/* Right side */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.875rem' }}>
          {/* Trust Score Indicator */}
          <div
            id="trust-score-indicator"
            style={{
              display: 'flex', alignItems: 'center', gap: '0.5rem',
              padding: '4px 10px', borderRadius: 999,
              background: 'var(--bg-card)', border: '1px solid var(--border)',
              cursor: 'pointer',
            }}
            onClick={() => setShowReplay(true)}
            title={`Trust Score: ${Number(trustScore).toFixed(0)}/100 (Tier: ${autonomyTier.toUpperCase()}) -- Click to open Replay`}
          >
            <div style={{
              width: 8, height: 8, borderRadius: '50%',
              backgroundColor:
                trustScore >= 70 ? 'var(--success)' :
                trustScore >= 40 ? 'var(--warning)' : 'var(--danger)',
              boxShadow: `0 0 6px ${
                trustScore >= 70 ? 'var(--success)' :
                trustScore >= 40 ? 'var(--warning)' : 'var(--danger)'
              }`,
            }} />
            <span className="text-xs font-semi" style={{ color: 'var(--text-secondary)' }}>
              Trust: {Number(trustScore).toFixed(0)}
            </span>
            <span style={{
              fontSize: '0.65rem',
              fontWeight: 700,
              padding: '1px 6px',
              borderRadius: 4,
              backgroundColor:
                trustScore >= 70 ? 'rgba(16,185,129,0.15)' :
                trustScore >= 40 ? 'rgba(245,158,11,0.15)' : 'rgba(239,68,68,0.15)',
              color:
                trustScore >= 70 ? 'var(--success)' :
                trustScore >= 40 ? 'var(--warning)' : 'var(--danger)',
              textTransform: 'uppercase',
            }}>
              {autonomyTier}
            </span>
          </div>

          {/* Audit Replay Button */}
          <button
            id="open-replay-btn"
            className="btn btn-secondary btn-sm"
            style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.75rem' }}
            onClick={() => setShowReplay(true)}
            title="Open session timeline replay"
          >
            <History size={14} />
            <span>Audit Replay</span>
          </button>

          <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
            <ShieldCheck size={14} color="var(--success)" />
            <span className="text-xs" style={{ color: 'var(--success)' }}>Policy Gate Active</span>
          </div>

          <div className="text-xs text-muted" style={{
            padding: '4px 10px', borderRadius: 999,
            background: 'var(--bg-card)', border: '1px solid var(--border)',
          }}>
            Session: {sessionId.slice(0, 8)}...
          </div>

          <button className="btn btn-ghost btn-sm" onClick={handleNewSession}>
            New Cart
          </button>
        </div>
      </header>

      {/* Buildathon banner */}
      <div style={{
        background: 'linear-gradient(90deg, rgba(99,102,241,0.15), rgba(16,185,129,0.1))',
        borderBottom: '1px solid var(--border)',
        padding: '0.5rem 2rem',
        display: 'flex', alignItems: 'center', gap: '0.5rem',
        fontSize: '0.75rem', color: 'var(--text-muted)',
      }}>
        <span style={{ color: 'var(--accent-light)', fontWeight: 600 }}>Razorpay AI Buildathon</span>
        <span>•</span>
        <span>Agentic Commerce Track</span>
        <span>•</span>
        <span>Bounded Autonomy + Trust Score + Full Auditability</span>
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

        {/* Right: Proposal Panel + Checkout */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', position: 'sticky', top: 80 }}>
          <ProposalPanel
            sessionId={sessionId}
            onCartUpdate={setCart}
            onRefreshCart={handleRefreshCart}
          />
          <div className="card" style={{ padding: '1.25rem' }}>
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
        <span>TrustCart • Phase 2 • Razorpay AI Buildathon 2026</span>
        <div style={{ display: 'flex', gap: '1rem' }}>
          <a href="/api/docs" target="_blank" rel="noreferrer" style={{ color: 'var(--text-muted)', textDecoration: 'none' }}>
            API Docs
          </a>
          <a href={`/api/audit/${sessionId}`} target="_blank" rel="noreferrer" style={{ color: 'var(--text-muted)', textDecoration: 'none' }}>
            Audit Log
          </a>
          <button
            onClick={() => setShowReplay(true)}
            style={{ background: 'none', border: 'none', color: 'var(--accent-light)', cursor: 'pointer', fontSize: '0.75rem', padding: 0 }}
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
