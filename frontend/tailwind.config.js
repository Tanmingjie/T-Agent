/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#ecfeff", 100: "#cffafe", 200: "#a5f3fc",
          300: "#67e8f9", 400: "#22d3ee", 500: "#06b6d4",
          600: "#0891b2", 700: "#0e7490", 800: "#155e75",
          900: "#164e63", 950: "#083344",
        },
        surface: {
          50: "#f8fafc", 100: "#f1f5f9", 200: "#e2e8f0",
          700: "#334155", 800: "#1e293b", 850: "#172033",
          900: "#0f172a", 950: "#020617",
        },
      },
      boxShadow: {
        card: "0 1px 3px 0 rgb(0 0 0 / 0.06), 0 1px 2px -1px rgb(0 0 0 / 0.06)",
        elevated: "0 4px 6px -1px rgb(0 0 0 / 0.08), 0 2px 4px -2px rgb(0 0 0 / 0.05)",
      },
    },
  },
  plugins: [],
};
