import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { AuthGate } from './auth/AuthGate'
import './index.css'
import './commandCenter.css'
import './deskLayout.css'
import './wall/wall.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AuthGate>
      <App />
    </AuthGate>
  </React.StrictMode>,
)
