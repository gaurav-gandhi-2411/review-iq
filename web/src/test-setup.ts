import '@testing-library/jest-dom/vitest'
import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'

// Testing Library only auto-cleans when the test runner exposes globals; we import
// from 'vitest' explicitly (no globals), so unmount between tests ourselves.
afterEach(() => cleanup())
