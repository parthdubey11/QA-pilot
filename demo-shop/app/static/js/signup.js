document.getElementById('signup-form').addEventListener('submit', async (event) => {
  event.preventDefault()
  const error = document.getElementById('signup-error')
  const success = document.getElementById('signup-success')
  error.textContent = ''
  success.textContent = ''
  const data = Object.fromEntries(new FormData(event.target))
  try {
    await api('/api/signup', { method: 'POST', body: JSON.stringify(data) })
    event.target.reset()
    success.innerHTML = 'Account created. You can now <a href="/login">log in</a>.'
  } catch (err) {
    error.textContent = err.message
  }
})
