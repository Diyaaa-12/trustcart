import { useState } from 'react';
import { CreditCard, AlertCircle, CheckCircle, Lock } from 'lucide-react';
import type { Cart, CheckoutResult } from '../types';
import { createCheckout } from '../api/client';

interface Props {
  cart: Cart | null;
  sessionId: string;
}

const formatINR = (n: number) =>
  new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(n);

type CheckoutState = 'idle' | 'loading' | 'success' | 'error';

export default function CheckoutButton({ cart, sessionId }: Props) {
  const [state, setState] = useState<CheckoutState>('idle');
  const [result, setResult] = useState<CheckoutResult | null>(null);
  const [errorMsg, setErrorMsg] = useState<string>('');

  const canCheckout = (cart?.item_count ?? 0) > 0;

  const handleCheckout = async () => {
    setState('loading');
    setErrorMsg('');
    try {
      const order = await createCheckout(sessionId);
      setResult(order);

      if (order.mock_mode) {
        // Mock mode â€” show success state directly
        setState('success');
        return;
      }

      // Real Razorpay mode â€” open checkout modal
      if (typeof (window as unknown as Record<string, unknown>)['Razorpay'] !== 'undefined') {
        const Razorpay = (window as unknown as Record<string, unknown>)['Razorpay'] as new (opts: unknown) => { open(): void };
        const rzp = new Razorpay({
          key: order.razorpay_key_id,
          amount: order.amount_paise,
          currency: order.currency,
          order_id: order.order_id,
          name: 'TrustCart',
          description: 'Your TrustCart Order',
          theme: { color: '#6366f1' },
          handler: () => setState('success'),
        });
        rzp.open();
      } else {
        // Razorpay.js not loaded â€” show order ID
        setState('success');
      }
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string | { error?: string } } } })
        ?.response?.data?.detail;
      if (detail && typeof detail === 'object' && 'error' in detail) {
        setErrorMsg(detail.error as string);
      } else if (typeof detail === 'string') {
        setErrorMsg(detail);
      } else {
        setErrorMsg('Payment service is temporarily unavailable. Your cart is saved â€” please try again.');
      }
      setState('error');
    }
  };

  const handleRetry = () => {
    setState('idle');
    setResult(null);
    setErrorMsg('');
  };

  if (state === 'success' && result) {
    return (
      <div style={{
        padding: '1.5rem', borderRadius: 16,
        background: 'var(--success-bg)', border: '1px solid var(--success-border)',
        display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '0.75rem',
        textAlign: 'center',
      }}>
        <CheckCircle size={32} color="var(--success)" />
        <div>
          <div className="text-base font-semi" style={{ color: 'var(--success)' }}>
            {result.mock_mode ? 'Demo Order Created!' : 'Order Placed!'}
          </div>
          <div className="text-xs text-muted" style={{ marginTop: 4 }}>
            Order ID: <code style={{ color: 'var(--text-secondary)' }}>{result.order_id}</code>
          </div>
          {result.mock_mode && (
            <div className="badge badge-warning" style={{ marginTop: 8 }}>
              Mock Mode â€” add Razorpay keys to .env for real payments
            </div>
          )}
        </div>
      </div>
    );
  }

  if (state === 'error') {
    return (
      <div style={{
        padding: '1.25rem', borderRadius: 12,
        background: 'var(--danger-bg)', border: '1px solid var(--danger-border)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem' }}>
          <AlertCircle size={16} color="var(--danger)" />
          <span className="text-sm font-semi" style={{ color: 'var(--danger)' }}>Payment Failed</span>
        </div>
        <div className="text-xs" style={{ color: 'var(--text-secondary)', marginBottom: '0.875rem', lineHeight: 1.5 }}>
          {errorMsg}
        </div>
        <div className="text-xs" style={{
          padding: '0.5rem 0.75rem', borderRadius: 8,
          background: 'rgba(16,185,129,0.08)', border: '1px solid rgba(16,185,129,0.2)',
          color: 'var(--success)', marginBottom: '0.875rem',
        }}>
          âœ“ Your cart is preserved â€” nothing was lost.
        </div>
        <button className="btn btn-ghost btn-sm w-full" onClick={handleRetry}>
          Try Again
        </button>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
      {cart && cart.item_count > 0 && (
        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          padding: '0.875rem 1rem', borderRadius: 10,
          background: 'var(--bg-input)', border: '1px solid var(--border)',
        }}>
          <div>
            <div className="text-xs text-muted">Total ({cart.item_count} items)</div>
            <div className="text-xl font-bold" style={{ color: 'var(--accent-light)' }}>
              {formatINR(cart.subtotal)}
            </div>
          </div>
          <div className="text-xs text-muted" style={{ textAlign: 'right' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 4, justifyContent: 'flex-end' }}>
              <Lock size={11} />
              Server-verified total
            </div>
          </div>
        </div>
      )}

      <button
        id="checkout-btn"
        className="btn btn-primary btn-lg w-full"
        onClick={handleCheckout}
        disabled={state === 'loading' || !canCheckout}
      >
        {state === 'loading' ? (
          <><div className="spinner" style={{ borderTopColor: '#fff' }} /> Processingâ€¦</>
        ) : (
          <><CreditCard size={17} /> Pay with Razorpay</>
        )}
      </button>

      {!canCheckout && (
        <div className="text-xs text-muted" style={{ textAlign: 'center' }}>
          Add items to your cart to proceed
        </div>
      )}
    </div>
  );
}
