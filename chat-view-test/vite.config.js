import { defineConfig } from 'vite'

// La API no tiene CORS: la vista llama a /api/* y Vite lo reenvía al microservicio (mismo origen).
// Aplica tanto a `vite` (desarrollo) como a `vite preview` (contenedor).
const target = process.env.API_URL || 'http://localhost:8000'

const proxy = {
  '/api': {
    target,
    changeOrigin: true,
    timeout: 180000,
    proxyTimeout: 180000, // una respuesta encadena varias llamadas al LLM
    rewrite: (path) => path.replace(/^\/api/, ''),
  },
}

export default defineConfig({
  server: { port: 5173, proxy },
  preview: {
    host: true,          // escuchar en 0.0.0.0 dentro del contenedor
    port: 8080,
    allowedHosts: true,  // acepta cualquier Host (IP, dominio, proxy inverso externo)
    proxy,
  },
})
