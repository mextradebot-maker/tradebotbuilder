// Genera public/tailwind.css (reemplaza el CDN cdn.tailwindcss.com de public/index.html).
// Regenerar tras agregar clases nuevas, desde la raiz de tradebotbuilder:
//   npx --yes tailwindcss@3.4.17 -c tools/tailwind/tailwind.config.js -i tools/tailwind/input.css -o public/tailwind.css --minify
// Las clases se leen de index.html y de los .js de public/ (no de vendor/).
module.exports = {
  content: ['./public/index.html', './public/*.js'],
  theme: { extend: {} },
  plugins: [],
}
