const removeIcon = '<svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14"/></svg>'

function renderCart(cart) {
  const rows = document.getElementById('cart-rows')
  rows.innerHTML = ''
  document.getElementById('cart-empty').hidden = cart.items.length > 0
  document.getElementById('cart-table').hidden = cart.items.length === 0
  for (const item of cart.items) {
    const tr = document.createElement('tr')
    tr.dataset.productId = item.product_id
    tr.innerHTML = `
      <td>${item.name}</td>
      <td>${formatMoney(item.unit_price)}</td>
      <td><input type="number" min="1" value="${item.quantity}" aria-label="Quantity for ${item.name}" data-action="set-quantity"></td>
      <td>${formatMoney(item.line_total)}</td>
      <td><button type="button" class="remove-btn" data-action="remove">${removeIcon}</button></td>`
    rows.appendChild(tr)
  }
  document.getElementById('cart-total').textContent = formatMoney(cart.total)
  document.getElementById('cart-count').textContent = cart.count
}

document.getElementById('cart-rows').addEventListener('change', async (event) => {
  if (!event.target.matches('[data-action="set-quantity"]')) return
  const productId = event.target.closest('tr').dataset.productId
  const quantity = parseInt(event.target.value, 10)
  const cart = await api(`/api/cart/items/${productId}`, {
    method: 'PATCH',
    body: JSON.stringify({ quantity }),
  })
  renderCart(cart)
})

document.getElementById('cart-rows').addEventListener('click', async (event) => {
  const button = event.target.closest('[data-action="remove"]')
  if (!button) return
  const row = button.closest('tr')
  const cart = await api(`/api/cart/items/${row.dataset.productId}`, { method: 'DELETE' })
  row.remove()
  document.getElementById('cart-count').textContent = cart.count
  if (cart.items.length === 0) {
    document.getElementById('cart-table').hidden = true
    document.getElementById('cart-empty').hidden = false
  }
})

api('/api/cart').then(renderCart)
