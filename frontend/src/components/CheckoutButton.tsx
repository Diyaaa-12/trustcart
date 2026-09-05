import { useState } from 'react';
import { CreditCard, AlertCircle, CheckCircle, ShieldCheck } from 'lucide-react';
import type { Cart, CheckoutResult } from '../types';
import { createCheckout } from '../api/client';

interface Props {
  cart: Cart | null;
  sessionId: string;
}

interface RazorpayPaymentSuccessResponse {
  razorpay_payment_id: string;
  razorpay_order_id: string;
  razorpay_signature: string;
}

interface RazorpayPaymentFailedResponse {
  error: {
    code?: string;
    description?: string;
    source?: string;
    step?: string;
    reason?: string;
    metadata?: {
      order_id?: string;
      payment_id?: string;
    };
  };
}

interface RazorpayOptions {
  key: string;
  amount: number;
  currency: string;
  name: string;
  description?: string;
  order_id: string;
  handler: (response: RazorpayPaymentSuccessResponse) => void;
  modal?: {
    ondismiss?: () => void;
    escape?: boolean;
    backdropclose?: boolean;
  };
  theme?: {
    color?: string;
  };
}

interface RazorpayInstance {
  open(): void;
  on(event: 'payment.failed', handler: (response: RazorpayPaymentFailedResponse) => void): void;
}

const formatINR = (n: number | string) =>
  new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(Number(n) || 0);

type CheckoutState = 'idle' | 'loading' | 'success' | 'error';

const loadRazorpayScript = (): Promise<boolean> => {
  return new Promise((resolve) => {
    if (typeof (window as unknown as { Razorpay?: unknown }).Razorpay !== 'undefined') {
      resolve(true);
      return;
    }

    const scriptSrc = 'https://checkout.razorpay.com/v1/checkout.js';
    const existing = document.querySelector(`script[src="${scriptSrc}"]`);
    if (existing) {
      if ((existing as HTMLScriptElement).dataset.loaded === 'true') {
        resolve(true);
        return;
      }
      existing.addEventListener('load', () => resolve(true));
      existing.addEventListener('error', () => resolve(false));
      return;
    }

    const script = document.createElement('script');
    script.src = scriptSrc;
    script.async = true;
    script.onload = () => {
      script.dataset.loaded = 'true';
      resolve(true);
    };
    script.onerror = () => {
      resolve(false);
    };
    document.body.appendChild(script);
  });
};

export default function CheckoutButton({ cart, sessionId }: Props) {
  const [state, setState] = useState<CheckoutState>('idle');
  const [result, setResult] = useState<CheckoutResult | null>(null);
  const [paymentId, setPaymentId] = useState<string>('');
  const [errorMsg, setErrorMsg] = useState<string>('');

  const canCheckout = (cart?.item_count ?? 0) > 0;

  const handleCheckout = async () => {
    setState('loading');
    setErrorMsg('');
    try {
      const order = await createCheckout(sessionId);
      setResult(order);

      if (order.mock_mode || !order.razorpay_key_id || order.razorpay_key_id === 'mock') {
        // Mock mode -- show success state directly (keep mock-mode fallback unchanged)
        setState('success');
        return;
      }

      // Real / test-mode Razorpay checkout
      const loaded = await loadRazorpayScript();
      if (!loaded) {
        setErrorMsg('Failed to load Razorpay checkout script. Please check your network connection.');
        setState('error');
        return;
      }

      const RazorpayConstructor = (window as unknown as {
        Razorpay: new (opts: RazorpayOptions) => RazorpayInstance;
      }).Razorpay;

      if (!RazorpayConstructor) {
        setErrorMsg('Razorpay payment gateway is not initialized.');
        setState('error');
        return;
      }

      const keyId =
        ((import.meta as unknown as { env?: Record<string, string> }).env?.VITE_RAZORPAY_KEY_ID) ||
        order.razorpay_key_id;

      let paymentHandled = false;

      const options: RazorpayOptions = {
        key: keyId,
        amount: order.amount_paise,
        currency: order.currency || 'INR',
        name: 'TrustCart',
        description: `Order Settlement (${order.order_id})`,
        order_id: order.order_id,
        handler: (response: RazorpayPaymentSuccessResponse) => {
          paymentHandled = true;
          setPaymentId(response.razorpay_payment_id);
          setState('success');
        },
        modal: {
          ondismiss: () => {
            if (!paymentHandled) {
              setState('error');
              setErrorMsg('Payment was cancelled -- the checkout window was closed before completion.');
            }
          },
          escape: true,
          backdropclose: false,
        },
        theme: {
          color: '#14B8A6',
        },
      };

      const rzp = new RazorpayConstructor(options);
      rzp.on('payment.failed', (response: RazorpayPaymentFailedResponse) => {
        paymentHandled = true;
        const desc = response?.error?.description || response?.error?.reason || 'Payment was declined or failed.';
        setErrorMsg(`Payment Failed: ${desc}`);
        setState('error');
      });

      rzp.open();
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
    setPaymentId('');
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
            {result.mock_mode ? 'Demo Order Created!' : 'Payment Successful!'}
          </div>
          <div className="text-xs text-muted" style={{ marginTop: 4 }}>
            Order ID: <code style={{ color: 'var(--text-secondary)' }}>{result.order_id}</code>
          </div>
          {paymentId && (
            <div className="text-xs text-muted" style={{ marginTop: 4 }}>
              Payment ID: <code style={{ color: 'var(--text-secondary)' }}>{paymentId}</code>
            </div>
          )}
          {result.mock_mode ? (
            <div className="badge badge-warning" style={{ marginTop: 8 }}>
              Mock Mode -- add Razorpay keys to .env for real payments
            </div>
          ) : (
            <div style={{
              marginTop: 8,
              display: 'inline-flex',
              alignItems: 'center',
              gap: 5,
              padding: '0.25rem 0.65rem',
              borderRadius: 6,
              background: 'rgba(16,185,129,0.12)',
              border: '1px solid rgba(16,185,129,0.25)',
              color: 'var(--success)',
              fontSize: '0.75rem',
              fontWeight: 500,
            }}>
              <ShieldCheck size={13} />
              <span>Razorpay Payment Verified</span>
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
          <span className="text-sm font-semi" style={{ color: 'var(--danger)' }}>Payment Incomplete</span>
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

      {canCheckout && (
        <div className="text-xs text-muted" style={{ textAlign: 'center', fontSize: '0.72rem' }}>
          Test card: <code>4111 1111 1111 1111</code> {"\u00B7"} Any future expiry {"\u00B7"} Any CVV
        </div>
      )}

      {!canCheckout && (
        <div className="text-xs text-muted" style={{ textAlign: 'center' }}>
          Add items to your cart to proceed with checkout
        </div>
      )}
    </div>
  );
}
