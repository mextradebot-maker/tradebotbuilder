// Iconos de linea del panel de alumnos: se suman a MTB_ICO y llevan su propio estilo.
// Lo cargan public/index.html y plantillas/panel-alumnos.html. Uso en texto: mtbI('zap') o mtbI('chart','mtb-ico-b').
(function () {
  var I = (window.MTB_ICO = window.MTB_ICO || {});
  var S = function (p) { return '<svg class="mtb-ico" viewBox="0 0 24 24" aria-hidden="true">' + p + '</svg>'; };
  var N = {
    zap: '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
    sliders: '<path d="M3 7h9M16 7h5M3 17h5M12 17h9"/><circle cx="14" cy="7" r="2.2"/><circle cx="10" cy="17" r="2.2"/>',
    down: '<path d="M12 4v11"/><path d="m7 11 5 5 5-5"/><path d="M5 20h14"/>',
    home: '<path d="M3 11.5 12 4l9 7.5"/><path d="M5.5 10v9.5h13V10"/><path d="M10 19.5v-5h4v5"/>',
    pulse: '<path d="M3 12h4l2.5-6 4 12 2.5-6H21"/>',
    chart: '<path d="M3 3v18h18"/><path d="M8 17v-5M13 17V8M18 17v-9"/>',
    clip: '<rect x="5.5" y="4" width="13" height="17" rx="2.2"/><path d="M9 4h6v3H9z"/><path d="M9 12h6M9 16h4"/>',
    layers: '<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/>',
    cards: '<rect x="4" y="7" width="12" height="14" rx="2.2"/><path d="M8 4h10a2 2 0 0 1 2 2v11"/>',
    trophy:'<path d="M8 4h8v5a4 4 0 0 1-8 0z"/><path d="M8 6H4v1a3 3 0 0 0 4 2.8M16 6h4v1a3 3 0 0 1-4 2.8"/><path d="M12 13v4M10 17h4M8.5 20h7"/>',
    alert: '<path d="M12 3.5 2.5 20h19z"/><path d="M12 10v4.5"/><path d="M12 17.4v.1"/>',
    x: '<path d="m6 6 12 12M18 6 6 18"/>',
    check: '<path d="m5 12.5 4.5 4.5L19 7"/>',
    menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
    moon: '<path d="M20 13.2A8 8 0 1 1 10.8 4a6.3 6.3 0 0 0 9.2 9.2z"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5.3 5.3l1.6 1.6M17.1 17.1l1.6 1.6M5.3 18.7l1.6-1.6M17.1 6.9l1.6-1.6"/>'
  };
  for (var k in N) if (!I[k]) I[k] = S(N[k]);
  window.mtbI = function (n, c) { return (I[n] || '').replace('class="mtb-ico"', 'class="mtb-ico ' + (c || 'mtb-ico-i') + '"'); };

  var st = document.createElement('style');
  st.textContent =
    '.mtb-ico{width:17px;height:17px;flex:none;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}' +
    '.mtb-ico-i{display:inline-block;width:1.1em;height:1.1em;margin-right:.4em;vertical-align:-.2em}' +
    '.mtb-ico-t{margin:0 0 0 .4em}' +
    '.mtb-ico-b{width:22px;height:22px}' +
    '.mtb-dot{display:inline-block;width:.55em;height:.55em;border-radius:50%;background:currentColor;margin-right:.5em;vertical-align:.05em;box-shadow:0 0 0 3px color-mix(in srgb,currentColor 22%,transparent)}' +
    '.mtb-dot-ok{color:#22c55e}';
  (document.head || document.documentElement).appendChild(st);
})();
