const productId = Number(window.location.pathname.split('/').pop())

async function renderProduct() {
  let product
  try {
    product = await api(`/api/products/${productId}`)
  } catch {
    document.getElementById('not-found').hidden = false
    return
  }
  document.title = `${product.name} | Demo Shop`
  document.getElementById('product-name').textContent = product.name
  const image = document.getElementById('product-image')
  image.src = product.image
  image.alt = product.name
  document.getElementById('product-price').innerHTML = product.sale_price !== null
    ? `${formatMoney(product.sale_price)} <s>${formatMoney(product.price)}</s>`
    : formatMoney(product.price)
  document.getElementById('product-description').textContent = product.description
  const stock = document.getElementById('product-stock')
  if (product.stock > 0) {
    stock.textContent = `In stock (${product.stock} left)`
  } else {
    stock.textContent = 'Out of stock'
    stock.className = 'stock-out'
  }
  document.getElementById('product-detail').hidden = false
}

document.getElementById('add-form').addEventListener('submit', async (event) => {
  event.preventDefault()
  const status = document.getElementById('add-status')
  const quantity = parseInt(document.getElementById('quantity').value, 10) || 1
  try {
    await api('/api/cart/items', {
      method: 'POST',
      body: JSON.stringify({ product_id: productId, quantity }),
    })
    status.textContent = `Added ${quantity} to your cart.`
  } catch (err) {
    status.textContent = err.message
  }
})

renderProduct()
