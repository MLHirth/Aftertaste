import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { MaybeClerkProvider } from './auth/clerk'
import './index.css'
import App from './App'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <MaybeClerkProvider>
      <App />
    </MaybeClerkProvider>
  </StrictMode>,
)
