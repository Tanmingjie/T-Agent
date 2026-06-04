/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        // TestSprite 沙绿(主色 600=#478d54),柔和沉静,围绕它生成的色阶
        brand: {
          50: "#f4f8f5", 100: "#e3eee5", 200: "#cbdfcf",
          300: "#acccb2", 400: "#86b48e", 500: "#619d6c",
          600: "#478d54", 700: "#397143", 800: "#2d5935",
          900: "#25492c", 950: "#18301d",
        },
        // 页面画布底色(灰底白卡,营造层次;TestSprite 风)
        canvas: "#f5f6f8",
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
