/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        background: '#05050f',
        surface: 'rgba(255, 255, 255, 0.05)',
        surfaceHover: 'rgba(255, 255, 255, 0.1)',
        primary: '#00f2fe',
        secondary: '#fe0979',
        neonGreen: '#00ff88',
        neonYellow: '#ffea00',
        neonRed: '#ff003c',
        textMain: '#ffffff',
        textMuted: '#a0a0b0',
      },
      boxShadow: {
        'neon-primary': '0 0 10px #00f2fe, 0 0 20px #00f2fe',
        'neon-secondary': '0 0 10px #fe0979, 0 0 20px #fe0979',
        'neon-green': '0 0 10px #00ff88, 0 0 20px #00ff88',
        'neon-yellow': '0 0 10px #ffea00, 0 0 20px #ffea00',
        'neon-red': '0 0 10px #ff003c, 0 0 20px #ff003c',
        'glass': '0 8px 32px 0 rgba(0, 0, 0, 0.37)',
      },
      backdropBlur: {
        'glass': '12px',
      },
      animation: {
        'fade-in': 'fadeIn 0.5s ease-out',
        'slide-up': 'slideUp 0.5s ease-out',
        'pulse-glow': 'pulseGlow 2s infinite',
      },
      keyframes: {
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        slideUp: {
          '0%': { opacity: '0', transform: 'translateY(20px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        pulseGlow: {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.6' },
        }
      }
    },
  },
  plugins: [],
}
