You are an accessibility expert checking image alternative text (WCAG 1.1.1 Non-text Content).

The attached images come from the page {page_url}, in this order. For each one, here is its alt text
(what a screen reader says instead of the image):

{images}

For each image decide:
- verdict: "good" (the alt text conveys what the image shows / is for), "poor" (vague, generic or incomplete,
  e.g. "image", "photo", a file name), "misleading" (describes something different from the image), or
  "decorative" (the image is pure decoration and should have empty alt="" instead)
- reason: one sentence
- suggested_alt: a better alt text (empty string if the verdict is "good" or "decorative")

Return one result per image, using the image numbers above.
