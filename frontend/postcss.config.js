// Tailwind v4: the PostCSS plugin moved to its own package, and autoprefixer is no
// longer needed (v4 handles prefixing internally via Lightning CSS).
module.exports = { plugins: { "@tailwindcss/postcss": {} } };
