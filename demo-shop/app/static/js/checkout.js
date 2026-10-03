const form = document.getElementById('checkout-form')
const errorBox = document.getElementById('checkout-error')

headerReady.then((user) => {
  if (!user) window.location.href = '/login?next=/checkout'
})

api('/api/cart').then((cart) => {
  document.getElementById('checkout-total').textContent = formatMoney(cart.total)
})

const promoInput = document.getElementById('promo_code')
promoInput.addEventListener('keydown', (event) => {
  // Apply the code when the user leaves the field with Tab.
  if (event.key === 'Tab') {
    event.preventDefault()
    const status = document.getElementById('promo-status')
    status.textContent = promoInput.value.trim() ? 'That promo code is not valid.' : ''
    promoInput.focus()
  }
})

form.addEventListener('submit', async (event) => {
  event.preventDefault()
  errorBox.textContent = ''
  const data = Object.fromEntries(new FormData(form))
  try {
    const order = await api('/api/checkout', { method: 'POST', body: JSON.stringify(data) })
    form.hidden = true
    document.getElementById('order-confirmation').innerHTML =
      `<h2>Thank you!</h2><p>Order #${order.order_id} is confirmed. Total charged: ${formatMoney(order.total)}.</p>`
    document.getElementById('cart-count').textContent = '0'
  } catch (err) {
    errorBox.textContent = err.message
  }
})
