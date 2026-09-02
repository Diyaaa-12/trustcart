import { useState } from 'react';
import { ShoppingCart, Plus, Trash2 } from 'lucide-react';
import type { Cart, Product } from '../types';
import { addToCart, removeFromCart } from '../api/client';

interface Props {
  cart: Cart | null;
  catalog: Product[];
  sessionId: string;
  onCartUpdate: (cart: Cart) => void;
}

const formatINR = (n: number) =>
  new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(n);

export default function CartView({ cart, catalog, sessionId, onCartUpdate }: Props) {
  const [adding, setAdding] = useState<number | null>(null);
  const [removing, setRemoving] = useState<number | null>(null);
  const [selectedCategory, setSelectedCategory] = useState<string>('All');

  const categories = ['All', ...Array.from(new Set(catalog.map(p => p.category))).sort()];
  const filteredCatalog = selectedCategory === 'All'
    ? catalog
    : catalog.filter(p => p.category === selectedCategory);

  const cartProductIds = new Set(cart?.items.map(i => i.product_id) ?? []);

  const handleAdd = async (productId: number) => {
    setAdding(productId);
    try {
      const updated = await addToCart(sessionId, productId);
      onCartUpdate(updated);
    } catch (err) {
      console.error('Failed to add item:', err);
    } finally {
      setAdding(null);
    }
  };

  const handleRemove = async (productId: number) => {
    setRemoving(productId);
    try {
      const updated = await removeFromCart(sessionId, productId);
      onCartUpdate(updated);
    } catch (err) {
      console.error('Failed to remove item:', err);
    } finally {
      setRemoving(null);
    }
  };

  const budgetUsed = cart?.discount_budget_used_pct ?? 0;
  const budgetRemaining = cart?.discount_budget_remaining_pct ?? 10;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>

      {/* â”€â”€ Current Cart â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */}
      <div className="card" style={{ padding: '1.5rem' }}>
        <div className="flex items-center justify-between" style={{ marginBottom: '1.25rem' }}>
          <div className="flex items-center gap-3">
            <div style={{
              width: 36, height: 36, borderRadius: 10,
              background: 'rgba(99,102,241,0.15)',
              display: 'flex', alignItems: 'center', justifyContent: 'center'
            }}>
              <ShoppingCart size={18} color="var(--accent-light)" />
            </div>
            <div>
              <div className="text-base font-semi">Your Cart</div>
              <div className="text-xs text-muted">{cart?.item_count ?? 0} items</div>
            </div>
          </div>
          {/* Discount budget indicator */}
          <div style={{ textAlign: 'right' }}>
            <div className="text-xs text-muted" style={{ marginBottom: 4 }}>Discount Budget</div>
            <div style={{
              width: 120, height: 6, background: 'var(--border)', borderRadius: 3, overflow: 'hidden'
            }}>
              <div style={{
                height: '100%',
                width: `${Math.min(100, (budgetUsed / 10) * 100)}%`,
                background: budgetRemaining > 3 ? 'var(--success)' : 'var(--warning)',
                borderRadius: 3,
                transition: 'width 0.4s ease',
              }} />
            </div>
            <div className="text-xs" style={{ marginTop: 3, color: 'var(--text-muted)' }}>
              {budgetRemaining.toFixed(1)}% remaining
            </div>
          </div>
        </div>

        {(!cart || cart.items.length === 0) ? (
          <div style={{
            textAlign: 'center', padding: '2rem 0',
            color: 'var(--text-muted)', fontSize: '0.875rem'
          }}>
            <ShoppingCart size={36} style={{ opacity: 0.3, marginBottom: 8 }} />
            <div>Your cart is empty</div>
            <div style={{ marginTop: 4, fontSize: '0.8rem' }}>Browse the catalog below to add items</div>
          </div>
        ) : (
          <>
            {cart.items.map(item => (
              <div key={item.id} className="slide-in" style={{
                display: 'flex', alignItems: 'center', gap: '0.75rem',
                padding: '0.75rem 0', borderBottom: '1px solid var(--border)'
              }}>
                <div style={{
                  width: 40, height: 40, borderRadius: 8,
                  background: 'var(--bg-input)', flexShrink: 0,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: '1.1rem'
                }}>
                  {categoryEmoji(item.category)}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="text-sm font-medium truncate">{item.product_name}</div>
                  <div className="chip" style={{ marginTop: 2 }}>{item.category}</div>
                </div>
                <div style={{ textAlign: 'right', flexShrink: 0 }}>
                  <div className="text-sm price">Ã—{item.quantity}</div>
                  <div className="text-xs text-secondary">{formatINR(item.line_total)}</div>
                </div>
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={() => handleRemove(item.product_id)}
                  disabled={removing === item.product_id}
                  title="Remove from cart"
                  style={{ padding: '0.35rem', borderColor: 'transparent' }}
                >
                  {removing === item.product_id
                    ? <div className="spinner" style={{ width: 14, height: 14 }} />
                    : <Trash2 size={14} color="var(--text-muted)" />
                  }
                </button>
              </div>
            ))}

            <div style={{
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              marginTop: '1rem', paddingTop: '0.75rem'
            }}>
              <div className="text-sm text-secondary">Subtotal</div>
              <div className="text-xl font-bold" style={{ color: 'var(--accent-light)' }}>
                {formatINR(cart.subtotal)}
              </div>
            </div>
          </>
        )}
      </div>

      {/* â”€â”€ Catalog Browser â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */}
      <div className="card" style={{ padding: '1.5rem' }}>
        <div className="text-base font-semi" style={{ marginBottom: '1rem' }}>Browse Catalog</div>

        {/* Category filter */}
        <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginBottom: '1rem' }}>
          {categories.map(cat => (
            <button
              key={cat}
              onClick={() => setSelectedCategory(cat)}
              style={{
                padding: '4px 12px', borderRadius: 999, fontSize: '0.75rem',
                fontWeight: 500, border: '1px solid',
                cursor: 'pointer', transition: 'all 150ms ease',
                background: selectedCategory === cat ? 'var(--accent)' : 'transparent',
                borderColor: selectedCategory === cat ? 'var(--accent)' : 'var(--border)',
                color: selectedCategory === cat ? '#fff' : 'var(--text-secondary)',
                fontFamily: 'inherit',
              }}
            >
              {cat}
            </button>
          ))}
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
          {filteredCatalog.map(product => {
            const inCart = cartProductIds.has(product.id);
            return (
              <div key={product.id}
                className="slide-in"
                style={{
                  display: 'flex', alignItems: 'center', gap: '0.75rem',
                  padding: '0.75rem', borderRadius: 10,
                  background: inCart ? 'rgba(99,102,241,0.06)' : 'var(--bg-input)',
                  border: `1px solid ${inCart ? 'rgba(99,102,241,0.25)' : 'transparent'}`,
                  transition: 'all 150ms ease',
                }}
              >
                <div style={{
                  width: 36, height: 36, borderRadius: 8,
                  background: 'var(--bg-card)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: '1rem', flexShrink: 0,
                }}>
                  {categoryEmoji(product.category)}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="text-sm font-medium truncate">{product.name}</div>
                  <div className="text-xs text-muted">{product.category} Â· Stock: {product.stock}</div>
                </div>
                <div className="text-sm font-semi" style={{ flexShrink: 0, color: 'var(--text-primary)' }}>
                  {formatINR(product.price)}
                </div>
                <button
                  className={`btn btn-sm ${inCart ? 'btn-ghost' : 'btn-primary'}`}
                  onClick={() => !inCart && handleAdd(product.id)}
                  disabled={adding === product.id || inCart}
                  style={{ flexShrink: 0 }}
                >
                  {adding === product.id
                    ? <div className="spinner" style={{ width: 14, height: 14 }} />
                    : inCart ? <><span>âœ“</span> Added</> : <><Plus size={13} /> Add</>
                  }
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function categoryEmoji(category: string): string {
  const map: Record<string, string> = {
    Electronics: 'âš¡',
    Accessories: 'ðŸŽ’',
    Clothing: 'ðŸ‘•',
    Footwear: 'ðŸ‘Ÿ',
    Books: 'ðŸ“š',
  };
  return map[category] ?? 'ðŸ“¦';
}
