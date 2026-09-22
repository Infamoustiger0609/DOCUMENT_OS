import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./lib/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        paper: "#F7F6F2",
        "paper-raised": "#FBFAF7",
        "sidebar-bg": "#F1EEE5",
        ink: "#1B1F3B",
        "ink-soft": "#5B5F79",
        line: "#E3E0D6",
        overdue: "#B23A34",
        "due-soon": "#B8842E",
        filed: "#3F6659",
        muted: "#A9A597",
      },
      fontFamily: {
        serif: ["var(--font-fraunces)", "serif"],
        sans: ["var(--font-plex-sans)", "sans-serif"],
      },
    },
  },
  plugins: [],
};

export default config;
