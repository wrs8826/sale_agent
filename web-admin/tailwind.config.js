/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
      colors: {
        primary: { DEFAULT: '#3b82f6', hover: '#2563eb' },
        sidebar: '#f7f7f8',
        border: '#e5e5e5',
        text: { main: '#171717', muted: '#6b6b6b', placeholder: '#9ca3af' },
      },
    },
  },
  plugins: [],
}
