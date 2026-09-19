/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#07090f",
          900: "#0c1018",
          800: "#141a26",
          700: "#1c2433",
        },
        paper: "#eef2ea",
        moss: "#7cffb2",
        rust: "#ff6a4a",
        pollen: "#f5c44e",
      },
      fontFamily: {
        display: ["Newsreader", "Georgia", "serif"],
        sans: ["IBM Plex Sans", "Segoe UI", "sans-serif"],
        mono: ["IBM Plex Mono", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
};
