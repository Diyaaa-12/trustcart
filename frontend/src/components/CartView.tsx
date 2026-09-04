import { useState, useRef, useEffect } from 'react';
import {
  ShoppingCart,
  Plus,
  Minus,
  Trash2,
  Cpu,
  Shirt,
  Footprints,
  BookOpen,
  Package,
  Layers,
  Check,
} from 'lucide-react';
import type { Cart, Product } from '../types';
import { addToCart, removeFromCart, updateCartItemQuantity } from '../api/client';

interface Props {
  cart: Cart | null;
  catalog: Product[];
  sessionId: string;
  onCartUpdate: (cart: Cart) => void;
}

const formatINR = (n: number | string) =>
  new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(Number(n) || 0);

function CategoryIcon({ category, size = 15 }: { category: string; size?: number }) {
  const iconProps = { size, style: { color: 'var(--accent)', flexShrink: 0 } };
  switch (category) {
    case 'Electronics':
      return <Cpu {...iconProps} />;
    case 'Accessories':
      return <Layers {...iconProps} />;
    case 'Clothing':
      return <Shirt {...iconProps} />;
    case 'Footwear':
      return <Footprints {...iconProps} />;
    case 'Books':
      return <BookOpen {...iconProps} />;
    default:
      return <Package {...iconProps} />;
  }
}

export default function CartView({ cart, catalog, sessionId, onCartUpdate }: Props) {
  const [adding, setAdding] = useState<number | null>(null);
  const [busyItemIds, setBusyItemIds] = useState<Record<number, boolean>>({});
  const [selectedCategory, setSelectedCategory] = useState<string>('All');

  // Ref to always hold latest server-authoritative cart state
  const latestCartRef = useRef<Cart | null>(cart);
  useEffect(() => {
    latestCartRef.current = cart;
  }, [cart]);

  // Per-item sequential mutation queue to eliminate races on rapid clicks
  const itemQueuesRef = useRef<Map<number, Promise<void>>>(new Map());

  const categories = ['All', ...Array.from(new Set(catalog.map(p => p.category))).sort()];
  const filteredCatalog = selectedCategory === 'All'
    ? catalog
    : catalog.filter(p => p.category === selectedCategory);

  const cartProductIds = new Set(cart?.items.map(i => i.product_id) ?? []);

  const handleAdd = async (productId: number) => {
    setAdding(productId);
    try {
      const updated = await addToCart(sessionId, productId);
      latestCartRef.current = updated;
      onCartUpdate(updated);
    } catch (err) {
      console.error('Failed to add item:', err);
    } finally {
      setAdding(null);
    }
  };

  const enqueueItemMutation = (
    productId: number,
    mutationFn: (currentCart: Cart | null) => Promise<Cart | null>
  ) => {
    // Set per-item loading state (disables buttons for this specific item only)
    setBusyItemIds(prev => ({ ...prev, [productId]: true }));

    const currentQueue = itemQueuesRef.current.get(productId) || Promise.resolve();

    const nextQueue = currentQueue
      .catch((err) => {
        console.warn(`Previous operation failed on product ${productId}:`, err);
      })
      .then(async () => {
        // Read authoritative server state from latestCartRef
        const currentCart = latestCartRef.current;
        const updatedCart = await mutationFn(currentCart);
        if (updatedCart) {
          latestCartRef.current = updatedCart;
          onCartUpdate(updatedCart);
        }
      })
      .catch((err) => {
        console.error(`Mutation failed for product ${productId}:`, err);
      })
      .finally(() => {
        // Clear busy state only if this was the last queued mutation for this item
        if (itemQueuesRef.current.get(productId) === nextQueue) {
          itemQueuesRef.current.delete(productId);
          setBusyItemIds(prev => {
            if (!prev[productId]) return prev;
            const copy = { ...prev };
            delete copy[productId];
            return copy;
          });
        }
      });

    itemQueuesRef.current.set(productId, nextQueue);
  };

  const handleDeltaQty = (productId: number, delta: number) => {
    enqueueItemMutation(productId, async (currentCart) => {
      const existingItem = currentCart?.items.find(i => i.product_id === productId);
      if (!existingItem && delta <= 0) {
        return null;
      }
      const currentQty = existingItem ? existingItem.quantity : 0;
      const targetQty = currentQty + delta;
      if (targetQty <= 0) {
        return await removeFromCart(sessionId, productId);
      }
      return await updateCartItemQuantity(sessionId, productId, targetQty);
    });
  };

  const handleRemove = (productId: number) => {
    enqueueItemMutation(productId, async () => {
      return await removeFromCart(sessionId, productId);
    });
  };

  const budgetUsed = Number(cart?.discount_budget_used_pct ?? 0);
  const budgetRemaining = Number(cart?.discount_budget_remaining_pct ?? 10);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>

      {/* Active Cart */}
      <div className="card" style={{ padding: '1.25rem 1.5rem' }}>
        <div className="flex items-center justify-between" style={{ marginBottom: '1.25rem' }}>
          <div className="flex items-center gap-3">
            <div style={{
              width: 34, height: 34, borderRadius: 6,
              background: 'var(--accent-bg)',
              border: '1px solid var(--accent-border)',
              display: 'flex', alignItems: 'center', justifyContent: 'center'
            }}>
              <ShoppingCart size={17} style={{ color: 'var(--accent)' }} />
            </div>
            <div>
              <div className="text-base font-semi">Active Cart</div>
              <div className="text-xs text-muted">{cart?.item_count ?? 0} item{cart?.item_count !== 1 ? 's' : ''}</div>
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
                width: `${Math.min(100, (Number(budgetUsed) / 10) * 100)}%`,
                background: Number(budgetRemaining) > 3 ? 'var(--accent)' : 'var(--warning)',
                borderRadius: 3,
                transition: 'width 0.3s ease',
              }} />
            </div>
            <div className="text-xs" style={{ marginTop: 3, color: 'var(--text-muted)' }}>
              {Number(budgetRemaining).toFixed(1)}% remaining
            </div>
          </div>
        </div>

        {(!cart || cart.items.length === 0) ? (
          <div style={{
            textAlign: 'center', padding: '2.5rem 0',
            color: 'var(--text-muted)', fontSize: '0.875rem'
          }}>
            <ShoppingCart size={32} style={{ opacity: 0.25, margin: '0 auto 8px', display: 'block' }} />
            <div>Your cart is empty</div>
            <div style={{ marginTop: 4, fontSize: '0.8rem' }}>Select products from the catalog below to add items</div>
          </div>
        ) : (
          <>
            {cart.items.map(item => {
              const isBusy = !!busyItemIds[item.product_id];
              return (
              <div key={item.id} className="slide-in" style={{
                display: 'flex', alignItems: 'center', gap: '0.875rem',
                padding: '0.75rem 0', borderBottom: '1px solid var(--border-subtle)'
              }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.45rem' }}>
                    <CategoryIcon category={item.category} size={15} />
                    <span className="text-sm font-medium truncate">{item.product_name}</span>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginTop: 2 }}>
                    <span className="category-label">{item.category}</span>
                    <span className="text-xs text-muted">{"\u00B7"} {formatINR(item.unit_price)} each</span>
                  </div>
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: '0.875rem', flexShrink: 0 }}>
                  {/* Quantity Stepper (- / qty / +) */}
                  <div style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    border: '1px solid var(--border)',
                    borderRadius: 6,
                    background: 'var(--bg-input)',
                    height: 28,
                  }}>
                    <button
                      className="btn btn-ghost"
                      onClick={() => handleDeltaQty(item.product_id, -1)}
                      disabled={isBusy}
                      title={item.quantity === 1 ? "Remove item" : "Decrease quantity"}
                      style={{
                        padding: '0 7px',
                        height: '100%',
                        border: 'none',
                        borderRadius: '6px 0 0 6px',
                        color: 'var(--text-secondary)',
                      }}
                    >
                      <Minus size={12} />
                    </button>
                    <span style={{
                      minWidth: 26,
                      textAlign: 'center',
                      fontSize: '0.75rem',
                      fontWeight: 600,
                      color: 'var(--text-primary)',
                      padding: '0 4px',
                    }}>
                      {isBusy ? (
                        <div className="spinner" style={{ width: 11, height: 11, margin: '0 auto' }} />
                      ) : (
                        item.quantity
                      )}
                    </span>
                    <button
                      className="btn btn-ghost"
                      onClick={() => handleDeltaQty(item.product_id, 1)}
                      disabled={isBusy}
                      title="Increase quantity"
                      style={{
                        padding: '0 7px',
                        height: '100%',
                        border: 'none',
                        borderRadius: '0 6px 6px 0',
                        color: 'var(--text-secondary)',
                      }}
                    >
                      <Plus size={12} />
                    </button>
                  </div>

                  {/* Line Total */}
                  <div style={{ textAlign: 'right', minWidth: 64 }}>
                    <div className="text-sm price">{formatINR(item.line_total)}</div>
                  </div>

                  {/* Delete Button */}
                  <button
                    className="btn btn-ghost btn-sm"
                    onClick={() => handleRemove(item.product_id)}
                    disabled={isBusy}
                    title="Remove from cart"
                    style={{ padding: '0.35rem', borderColor: 'transparent' }}
                  >
                    {isBusy ? (
                      <div className="spinner" style={{ width: 14, height: 14 }} />
                    ) : (
                      <Trash2 size={14} style={{ color: 'var(--text-muted)' }} />
                    )}
                  </button>
                </div>
              </div>
            );
          })}

            <div style={{
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              marginTop: '1rem', paddingTop: '0.5rem'
            }}>
              <div className="text-sm text-secondary">Subtotal</div>
              <div className="text-xl font-bold" style={{ color: 'var(--accent)' }}>
                {formatINR(cart.subtotal)}
              </div>
            </div>
          </>
        )}
      </div>

      {/* Merchant Catalog Browser -- Dense Inventory View */}
      <div className="card" style={{ padding: '1.25rem 1.5rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.85rem' }}>
          <div className="text-base font-semi">Merchant Inventory</div>
          <div className="text-xs text-muted">{filteredCatalog.length} items listed</div>
        </div>

        {/* Category filter */}
        <div style={{ display: 'flex', gap: '0.35rem', flexWrap: 'wrap', marginBottom: '0.85rem' }}>
          {categories.map(cat => (
            <button
              key={cat}
              onClick={() => setSelectedCategory(cat)}
              style={{
                padding: '3px 10px', borderRadius: 6, fontSize: '0.72rem',
                fontWeight: 600, border: '1px solid',
                cursor: 'pointer', transition: 'all 120ms ease',
                background: selectedCategory === cat ? 'var(--accent)' : 'transparent',
                borderColor: selectedCategory === cat ? 'var(--accent)' : 'var(--border)',
                color: selectedCategory === cat ? '#0F1420' : 'var(--text-secondary)',
                fontFamily: 'inherit',
              }}
            >
              {cat}
            </button>
          ))}
        </div>

        {/* Dense Inventory List */}
        <div style={{
          display: 'flex',
          flexDirection: 'column',
          borderRadius: 8,
          border: '1px solid var(--border)',
          overflow: 'hidden',
          background: 'var(--bg-input)',
        }}>
          {filteredCatalog.map((product, idx) => {
            const inCart = cartProductIds.has(product.id);
            const isLast = idx === filteredCatalog.length - 1;
            return (
              <div
                key={product.id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.75rem',
                  padding: '0.5rem 0.85rem',
                  background: inCart ? 'rgba(20, 184, 166, 0.04)' : idx % 2 === 0 ? 'rgba(255,255,255,0.015)' : 'transparent',
                  borderBottom: isLast ? 'none' : '1px solid var(--border-subtle)',
                  transition: 'background 100ms ease',
                }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.45rem' }}>
                    <CategoryIcon category={product.category} size={14} />
                    <span className="text-sm font-medium truncate">{product.name}</span>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', marginTop: 2 }}>
                    <span className="category-label">{product.category}</span>
                    <span className="text-xs text-muted">{"\u00B7"} Stock: {product.stock}</span>
                  </div>
                </div>

                <div className="text-sm font-semi" style={{ flexShrink: 0, color: 'var(--text-primary)', minWidth: 64, textAlign: 'right' }}>
                  {formatINR(product.price)}
                </div>

                <button
                  className={`btn btn-sm ${inCart ? 'btn-ghost' : 'btn-primary'}`}
                  onClick={() => !inCart && handleAdd(product.id)}
                  disabled={adding === product.id || inCart}
                  style={{ flexShrink: 0, height: 28, fontSize: '0.75rem', padding: '0 0.65rem' }}
                >
                  {adding === product.id ? (
                    <div className="spinner" style={{ width: 12, height: 12 }} />
                  ) : inCart ? (
                    <><Check size={12} /> Added</>
                  ) : (
                    <><Plus size={12} /> Add</>
                  )}
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
