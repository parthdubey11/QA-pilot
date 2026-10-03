function priceHtml(product) {
  if (product.sale_price !== null) {
    return `${formatMoney(product.sale_price)} <s>${formatMoney(product.price)}</s>`
  }
  return formatMoney(product.price)
}

function displayPrice(product) {
  return formatMoney(product.sale_price ?? product.price)
}

const sorters = {
  featured: () => 0,
  'price-asc': (a, b) => displayPrice(a).localeCompare(displayPrice(b)),
  'price-desc': (a, b) => displayPrice(b).localeCompare(displayPrice(a)),
  name: (a, b) => a.name.localeCompare(b.name),
}

let products = []

function renderProducts() {
  const sort = document.getElementById('sort').value
  const sorted = [...products].sort(sorters[sort])
  const grid = document.getElementById('product-grid')
  grid.innerHTML = ''
  for (const product of sorted) {
    const li = document.createElement('li')
    li.className = 'product-card'
    li.innerHTML = `
      <a href="/products/${product.id}">
        <img src="${product.image}" width="300" height="200">
        <h2>${product.name}</h2>
        <p class="price">${priceHtml(product)}</p>
      </a>`
    grid.appendChild(li)
  }
}

document.getElementById('sort').addEventListener('change', renderProducts)

api('/api/products').then((data) => {
  products = data
  renderProducts()
})
