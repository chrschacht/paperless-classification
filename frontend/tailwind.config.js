/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Bright blue accent and translucent neutral surfaces for the
        // Paperless Classification glass interface.
        primary: {
          50: '#eff6ff',
          100: '#dbeafe',
          200: '#bfdbfe',
          300: '#93c5fd',
          400: '#60a5fa',
          500: '#087ff5',
          600: '#0673e2',
          700: '#1e40af',
          800: '#1e3a8a',
          900: '#172554',
        },
        // Numeric roles preserve existing component semantics:
        // 900=canvas, 800=glass surface, 100=primary text.
        surface: {
          50: '#020617',
          100: '#0f172a',
          200: '#1e293b',
          300: '#34445d',
          400: '#53647e',
          500: '#6f8098',
          600: '#a8b5c7',
          700: '#d9e2ee',
          800: '#f8fbff',
          900: '#eaf1fb',
          950: '#e2ebf7',
        },
      },
      fontFamily: {
        sans: ['-apple-system', 'BlinkMacSystemFont', 'SF Pro Text', 'Helvetica Neue', 'Arial', 'sans-serif'],
        display: ['-apple-system', 'BlinkMacSystemFont', 'SF Pro Display', 'Helvetica Neue', 'Arial', 'sans-serif'],
      },
      animation: {
        'fade-in': 'fadeIn 0.5s ease-out',
        'slide-up': 'slideUp 0.4s ease-out',
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
      },
      keyframes: {
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        slideUp: {
          '0%': { opacity: '0', transform: 'translateY(10px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
      },
    },
  },
  plugins: [],
}
