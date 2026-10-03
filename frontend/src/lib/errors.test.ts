import { describe, expect, it } from 'vitest'
import { ApiError } from './api'
import { errorText } from './errors'

describe('errorText', () => {
  it('explains network failures, keeps API messages', () => {
    expect(errorText(new TypeError('Failed to fetch'))).toMatch(/Can't reach the QA Pilot server/)
    expect(errorText(new ApiError(422, "This address can't be tested"))).toBe("This address can't be tested")
    expect(errorText('weird')).toBe('Something went wrong. Please try again.')
  })
})
