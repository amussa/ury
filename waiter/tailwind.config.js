import preset from '@ury/ui/tailwind-preset';

/** @type {import('tailwindcss').Config} */
export default {
  content: [
    './index.html',
    './src/**/*.{js,ts,jsx,tsx}',
    '../packages/ui/src/**/*.{ts,tsx}',
  ],
  presets: [preset],
  theme: {
    extend: {
      boxShadow: {
        sheet: '0 -16px 48px rgba(15, 23, 42, 0.18)',
      },
    },
  },
  plugins: [],
};
