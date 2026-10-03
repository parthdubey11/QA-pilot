async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    ...options,
  })
  let data = null
  try { data = await res.json() } catch { /* empty body */ }
  if (!res.ok) {
    const detail = data && typeof data.detail === 'string' ? data.detail : 'Something went wrong. Please try again.'
    throw new Error(detail)
  }
  return data
}

function formatMoney(cents) {
  const sign = cents < 0 ? '-' : ''
  return `${sign}$${(Math.abs(cents) / 100).toFixed(2)}`
}

async function refreshCartCount() {
  const cart = await api('/api/cart')
  document.getElementById('cart-count').textContent = cart.count
}

async function loadHeader() {
  const { user } = await api('/api/me')
  document.querySelector('[data-auth="guest"]').hidden = Boolean(user)
  document.querySelector('[data-auth="user"]').hidden = !user
  if (user) document.querySelector('[data-user-name]').textContent = user.name
  await refreshCartCount()
  return user
}

document.addEventListener('click', async (event) => {
  if (event.target.closest('[data-action="logout"]')) {
    await api('/api/logout', { method: 'POST' })
    window.location.href = '/'
  }
})

const headerReady = loadHeader()
