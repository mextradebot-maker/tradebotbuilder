/* Pie de página corporativo y aviso de cookies, compartidos por todas las páginas públicas.
   Uso: <script src="/legal.js" defer></script> antes de </body>. Sin dependencias. */
(function () {
  var ANIO = new Date().getFullYear();
  var CLAVE_COOKIES = 'mtb_cookies_aceptadas';

  var css = '' +
    '.mtb-legal-footer{width:100%;margin-top:32px;padding:28px 20px 24px;border-top:1px solid #2a3a50;background:#080f1b;color:#a6b6cc;font:12px/1.6 "Lexend","Segoe UI",sans-serif}' +
    '.mtb-legal-footer *{box-sizing:border-box}' +
    '.mtb-legal-wrap{max-width:1440px;margin:0 auto;display:grid;grid-template-columns:1.3fr 1fr 1fr;gap:28px}' +
    '.mtb-legal-marca{color:#edf3fb;font-size:16px;font-weight:700;letter-spacing:-.4px}' +
    '.mtb-legal-lema{color:#32c7df;font-size:10px;letter-spacing:2px;text-transform:uppercase;margin:4px 0 10px}' +
    '.mtb-legal-footer h4{color:#edf3fb;font-size:11px;letter-spacing:1.4px;text-transform:uppercase;margin:0 0 10px;font-weight:600}' +
    '.mtb-legal-footer ul{list-style:none;margin:0;padding:0}' +
    '.mtb-legal-footer li{margin:0 0 6px}' +
    '.mtb-legal-footer a{color:#a6b6cc;text-decoration:none}' +
    '.mtb-legal-footer a:hover,.mtb-legal-footer a:focus-visible{color:#32c7df;text-decoration:underline}' +
    '.mtb-legal-logo{background:#fff;border-radius:10px;padding:6px 10px;display:inline-block;margin-bottom:10px}.mtb-legal-logo img{display:block;width:96px;height:auto}' +
    '.mtb-legal-redes{display:flex;gap:10px;margin-top:14px}.mtb-legal-redes a{display:inline-flex;align-items:center;justify-content:center;width:36px;height:36px;border:1px solid #2a3a50;border-radius:9px;color:#a6b6cc}.mtb-legal-redes a:hover,.mtb-legal-redes a:focus-visible{color:#32c7df;border-color:#32c7df;text-decoration:none}' +
    '.mtb-legal-riesgo{max-width:1440px;margin:22px auto 0;padding-top:16px;border-top:1px solid #2a3a50;font-size:11px;color:#899db9}' +
    '.mtb-legal-copy{max-width:1440px;margin:10px auto 0;font-size:11px;color:#899db9}' +
    '@media(max-width:800px){.mtb-legal-wrap{grid-template-columns:1fr 1fr}.mtb-legal-wrap>div:first-child{grid-column:1/-1}}' +
    '@media(max-width:480px){.mtb-legal-footer{padding:22px 16px}.mtb-legal-wrap{grid-template-columns:1fr;gap:18px}}' +
    '.mtb-cookies{position:fixed;left:16px;right:16px;bottom:16px;z-index:9999;max-width:720px;margin:0 auto;background:#101b2b;color:#edf3fb;border:1px solid #2a3a50;border-radius:12px;padding:16px 18px;display:flex;gap:14px;align-items:center;flex-wrap:wrap;box-shadow:0 10px 30px #0008;font:13px/1.5 "Lexend","Segoe UI",sans-serif}' +
    '.mtb-cookies p{margin:0;flex:1 1 300px;color:#a6b6cc}' +
    '.mtb-cookies a{color:#32c7df}' +
    '.mtb-cookies button{background:#32c7df;color:#06242c;border:0;border-radius:8px;padding:10px 18px;font:inherit;font-weight:600;cursor:pointer}';

  var REDES = [
    ['YouTube', 'https://www.youtube.com/channel/UCbka7h1ybRM2nuiILr0xRdA', '<path d="M22 8.2c0-1.6-1.3-2.9-2.9-3C17.3 5 14.6 5 12 5s-5.3 0-7.1.2C3.3 5.3 2 6.6 2 8.2 1.9 9.5 1.9 10.7 1.9 12s0 2.5.1 3.8c0 1.6 1.3 2.9 2.9 3 1.8.2 4.5.2 7.1.2s5.3 0 7.1-.2c1.6-.1 2.9-1.4 2.9-3 .1-1.3.1-2.5.1-3.8s0-2.5-.1-3.8zM10 15.2V8.8l5.5 3.2-5.5 3.2z"/>'],
    ['Facebook', 'https://www.facebook.com/mextradebot/', '<path d="M13.5 22v-8.2h2.8l.4-3.3h-3.2V8.4c0-.9.3-1.6 1.6-1.6h1.7V3.9c-.3 0-1.3-.1-2.5-.1-2.5 0-4.2 1.5-4.2 4.3v2.4H7.3v3.3h2.8V22h3.4z"/>'],
    ['Instagram', 'https://www.instagram.com/mextradebot/', '<path d="M12 7.3a4.7 4.7 0 1 0 0 9.4 4.7 4.7 0 0 0 0-9.4zm0 7.7a3 3 0 1 1 0-6 3 3 0 0 1 0 6zm4.9-8.9a1.1 1.1 0 1 0 0 2.2 1.1 1.1 0 0 0 0-2.2zM21.9 8c-.1-1.5-.4-2.8-1.5-3.9S17.9 2.6 16.4 2.5C14.8 2.4 9.2 2.4 7.6 2.5 6.1 2.6 4.8 2.9 3.7 4S2.2 6.5 2.1 8c-.1 1.6-.1 6.4 0 8 .1 1.5.4 2.8 1.5 3.9s2.4 1.4 3.9 1.5c1.6.1 7.2.1 8.8 0 1.5-.1 2.8-.4 3.9-1.5s1.4-2.4 1.5-3.9c.1-1.6.1-6.4 0-8zm-2 9.7a3.2 3.2 0 0 1-1.8 1.8c-1.3.5-4.3.4-5.7.4s-4.4.1-5.7-.4a3.2 3.2 0 0 1-1.8-1.8c-.5-1.3-.4-4.3-.4-5.7s-.1-4.4.4-5.7A3.2 3.2 0 0 1 6.7 4.5c1.3-.5 4.3-.4 5.7-.4s4.4-.1 5.7.4a3.2 3.2 0 0 1 1.8 1.8c.5 1.3.4 4.3.4 5.7s.1 4.4-.4 5.7z"/>'],
    ['TikTok', 'https://www.tiktok.com/@tezcaltlibot', '<path d="M16.6 2h-3.4v13.2a2.9 2.9 0 1 1-2.9-2.9c.3 0 .6 0 .9.1V9a6.3 6.3 0 1 0 5.4 6.2V8.6a7.9 7.9 0 0 0 4.6 1.5V6.7a4.6 4.6 0 0 1-4.6-4.7z"/>']
  ];

  function redes() {
    return '<div class="mtb-legal-redes">' + REDES.map(function (r) {
      return '<a href="' + r[1] + '" target="_blank" rel="noopener" aria-label="MexTradeBot en ' + r[0] + '" title="' + r[0] + '">' +
        '<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden="true">' + r[2] + '</svg></a>';
    }).join('') + '</div>';
  }

  function enlace(href, texto) {
    return '<li><a href="' + href + '">' + texto + '</a></li>';
  }

  function pie() {
    var f = document.createElement('footer');
    f.className = 'mtb-legal-footer';
    f.setAttribute('role', 'contentinfo');
    f.innerHTML =
      '<div class="mtb-legal-wrap">' +
        '<div><div class="mtb-legal-logo"><img src="/img/logo-mtb.png" alt="" width="320" height="187"></div>' +
        '<div class="mtb-legal-marca">MexTradeBot</div><div class="mtb-legal-lema">Trading agéntico</div>' +
        '<p style="margin:0;max-width:420px">Robots de trading para MetaTrader 5, análisis de mercado y formación para operar con método.</p>' + redes() + '</div>' +
        '<div><h4>Legal</h4><ul>' +
          enlace('/aviso-de-privacidad.html', 'Aviso de privacidad') +
          enlace('/politica-de-cookies.html', 'Política de cookies') +
          enlace('/aviso-de-riesgo.html', 'Aviso de riesgo y deslinde') +
        '</ul></div>' +
        '<div><h4>Contacto</h4><ul>' +
          '<li><a href="https://t.me/ricardopenac" target="_blank" rel="noopener">Telegram</a></li>' +
          enlace('/', 'Panel de alumnos') +
        '</ul></div>' +
      '</div>' +
      '<p class="mtb-legal-riesgo">Operar instrumentos financieros apalancados implica un alto riesgo y puede ocasionar la pérdida total del capital. ' +
        'Los resultados pasados y los backtests no garantizan resultados futuros. MexTradeBot no es asesor de inversiones ni intermediario financiero. ' +
        '<a href="/aviso-de-riesgo.html">Lee el aviso de riesgo completo</a>.</p>' +
      '<p class="mtb-legal-copy">© ' + ANIO + ' MexTradeBot. Todos los derechos reservados.</p>';
    return f;
  }

  function leerAceptadas() {
    try { return localStorage.getItem(CLAVE_COOKIES) === '1'; } catch (e) { return false; }
  }

  function avisoCookies() {
    if (leerAceptadas()) return;
    var d = document.createElement('div');
    d.className = 'mtb-cookies';
    d.setAttribute('role', 'region');
    d.setAttribute('aria-label', 'Aviso de cookies');
    d.innerHTML = '<p>Usamos cookies propias necesarias para mantener tu sesión y recordar tus preferencias, y servicios de terceros para fuentes y estilos. ' +
      'Consulta la <a href="/politica-de-cookies.html">política de cookies</a>.</p><button type="button">Entendido</button>';
    d.querySelector('button').addEventListener('click', function () {
      try { localStorage.setItem(CLAVE_COOKIES, '1'); } catch (e) {}
      d.remove();
    });
    document.body.appendChild(d);
  }

  function iniciar() {
    if (document.querySelector('.mtb-legal-footer')) return;
    var estilo = document.createElement('style');
    estilo.textContent = css;
    document.head.appendChild(estilo);
    // Las páginas del panel centran su contenido con body flex en fila; el pie va debajo.
    if (getComputedStyle(document.body).display === 'flex') {
      document.body.style.flexDirection = 'column';
      document.body.style.alignItems = 'stretch';
    }
    document.body.appendChild(pie());
    avisoCookies();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', iniciar);
  else iniciar();
})();
