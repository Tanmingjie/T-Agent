/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#ecfdf3", 100: "#d1fadf", 200: "#a6f4c5",
          300: "#6ce9a6", 400: "#32d583", 500: "#12b76a",
          600: "#039855", 700: "#027a48", 800: "#05603a",
          900: "#054f31", 950: "#053321",
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
