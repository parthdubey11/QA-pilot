export function errorText(error: unknown): string {
  // fetch() rejects with a TypeError ("Failed to fetch", "NetworkError…") when the server can't be reached at all.
  if (error instanceof TypeError) return "Can't reach the QA Pilot server. Check that it's running, then try again."
  return error instanceof Error ? error.message : 'Something went wrong. Please try again.'
}
