document.getElementById('login-form').addEventListener('submit', async (event) => {
  event.preventDefault()
  const error = document.getElementById('login-error')
  error.textContent = ''
  const data = Object.fromEntries(new FormData(event.target))
  try {
    await api('/api/login', { method: 'POST', body: JSON.stringify(data) })
    const next = new URLSearchParams(window.location.search).get('redirect')
    window.location.href = next && next.startsWith('/') && !next.startsWith('//') ? next : '/'
  } catch (err) {
    error.textContent = err.message
  }
})
