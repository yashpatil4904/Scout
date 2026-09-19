/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        sand: {
          50: "#f7f8fa",
          100: "#eef1f5",
          200: "#dde3eb",
          300: "#c5ced9",
        },
        ink: {
          950: "#0b0f14",
          900: "#121820",
          800: "#1a222d",
          700: "#2a3544",
          600: "#3d4d60",
          500: "#5a6b7d",
          400: "#7a8b9c",
        },
        sea: {
          DEFAULT: "#0f766e",
          bright: "#0d9488",
          soft: "#ccfbf1",
          mist: "#f0fdfa",
        },
        paper: "#f7f8fa",
        moss: "#0f766e",
        rust: "#dc4a3d",
        pollen: "#c47f0a",
      },
      fontFamily: {
        display: ["Syne", "system-ui", "sans-serif"],
        sans: ["Source Sans 3", "Segoe UI", "sans-serif"],
        mono: ["IBM Plex Mono", "ui-monospace", "monospace"],
      },
      boxShadow: {
        lift: "0 18px 50px -28px rgba(15, 23, 42, 0.35)",
      },
      keyframes: {
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(14px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "pulse-dot": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.45" },
        },
        drift: {
          "0%, 100%": { transform: "translateY(0)" },
          "50%": { transform: "translateY(-8px)" },
        },
      },
      animation: {
        "fade-up": "fade-up 0.7s ease-out both",
        "fade-up-delay": "fade-up 0.7s ease-out 0.12s both",
        "fade-up-late": "fade-up 0.7s ease-out 0.24s both",
        "pulse-dot": "pulse-dot 2s ease-in-out infinite",
        drift: "drift 6s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
