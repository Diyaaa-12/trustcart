import { useState } from 'react';
import { CreditCard, AlertCircle, CheckCircle, ShieldCheck } from 'lucide-react';
import type { Cart, CheckoutResult } from '../types';
import { createCheckout } from '../api/client';

interface Props {
  cart: Cart | null;
  sessionId: string;
}

const formatINR = (n: number | string) =>
  new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(Number(n) || 0);

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
        // Mock mode -- show success state directly
        setState('success');
        return;
      }

      // Real Razorpay mode -- open checkout modal
      if (typeof (window as unknown as Record<string, unknown>)['Razorpay'] !== 'undefined') {
        const Razorpay = (window as unknown as Record<string, unknown>)['Razorpay'] as new (opts: unknown) => { open(): void };
        const rzp = new Razorpay({
          key: order.razorpay_key_id,
          amount: order.amount_paise,
          currency: order.currency,
          order_id: order.order_id,
          name: 'TrustCart',
          description: 'Your TrustCart Order',
          theme: { color: '#14B8A6' },
          handler: () => setState('success'),
        });
        rzp.open();
      } else {
        // Razorpay.js not loaded -- show order ID
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
        setErrorMsg('Payment service is temporarily unavailable. Your cart is saved -- please try again.');
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
        padding: '1.5rem', borderRadius: 12,
        background: 'var(--success-bg)', border: '1px solid var(--success-border)',
        display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '0.75rem',
        textAlign: 'center',
      }}>
        <CheckCircle size={32} style={{ color: 'var(--success)' }} />
        <div>
          <div className="text-base font-semi" style={{ color: 'var(--success)' }}>
            {result.mock_mode ? 'Demo Order Created!' : 'Order Placed!'}
          </div>
          <div className="text-xs text-muted" style={{ marginTop: 4 }}>
            Order ID: <code style={{ color: 'var(--text-secondary)' }}>{result.order_id}</code>
          </div>
          {result.mock_mode && (
            <div className="badge badge-warning" style={{ marginTop: 8 }}>
              Mock Mode -- add Razorpay keys to .env for real payments
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
          <AlertCircle size={16} style={{ color: 'var(--danger)' }} />
          <span className="text-sm font-semi" style={{ color: 'var(--danger)' }}>Payment Failed</span>
        </div>
        <div className="text-xs" style={{ color: 'var(--text-secondary)', marginBottom: '0.875rem', lineHeight: 1.5 }}>
          {errorMsg}
        </div>
        <div className="text-xs" style={{
          padding: '0.5rem 0.75rem', borderRadius: 6,
          background: 'rgba(16,185,129,0.08)', border: '1px solid rgba(16,185,129,0.2)',
          color: 'var(--success)', marginBottom: '0.875rem',
          display: 'flex', alignItems: 'center', gap: 6,
        }}>
          <CheckCircle size={14} />
          <span>Your cart is preserved -- nothing was lost.</span>
        </div>
        <button className="btn btn-ghost btn-sm w-full" onClick={handleRetry}>
          Try Again
        </button>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.875rem' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 2 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <CreditCard size={17} style={{ color: 'var(--accent)' }} />
          <span className="text-base font-semi">Order Settlement</span>
        </div>
        <div className="chip" style={{ fontSize: '0.7rem' }}>
          <ShieldCheck size={12} />
          <span>Server Verified</span>
        </div>
      </div>

      {cart && cart.item_count > 0 && (
        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          padding: '1rem', borderRadius: 8,
          background: 'var(--bg-input)', border: '1px solid var(--border)',
        }}>
          <div>
            <div className="text-xs text-muted">Total Payable ({cart.item_count} item{cart.item_count !== 1 ? 's' : ''})</div>
            <div className="text-2xl font-bold" style={{ color: 'var(--text-primary)', marginTop: 2 }}>
              {formatINR(cart.subtotal)}
            </div>
          </div>
          <div className="text-xs text-muted" style={{ textAlign: 'right' }}>
            <div className="text-xs" style={{ color: 'var(--success)', fontWeight: 600 }}>0% Gateway Surcharge</div>
            <div className="text-xs text-muted">Direct merchant checkout</div>
          </div>
        </div>
      )}

      <button
        id="checkout-btn"
        className="btn btn-primary btn-lg w-full"
        onClick={handleCheckout}
        disabled={state === 'loading' || !canCheckout}
        style={{ height: 48, fontSize: '0.95rem' }}
      >
        {state === 'loading' ? (
          <><div className="spinner" style={{ borderTopColor: '#0F1420' }} /> Processing Settlement...</>
        ) : (
          <><CreditCard size={17} /> Pay with Razorpay</>
        )}
      </button>

      {!canCheckout && (
        <div className="text-xs text-muted" style={{ textAlign: 'center' }}>
          Add items to your cart to proceed with checkout
        </div>
      )}
    </div>
  );
}
