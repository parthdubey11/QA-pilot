document.getElementById('contact-form').addEventListener('submit', async (event) => {
  event.preventDefault()
  const error = document.getElementById('contact-error')
  const success = document.getElementById('contact-success')
  error.textContent = ''
  success.textContent = ''
  const data = Object.fromEntries(new FormData(event.target))
  try {
    await api('/api/contact', { method: 'POST', body: JSON.stringify(data) })
    event.target.reset()
    success.textContent = "Thanks! We'll get back to you within 2 working days."
  } catch (err) {
    error.textContent = err.message
  }
})
