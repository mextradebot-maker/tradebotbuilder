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
    '.mtb-legal-riesgo{max-width:1440px;margin:22px auto 0;padding-top:16px;border-top:1px solid #2a3a50;font-size:11px;color:#899db9}' +
    '.mtb-legal-copy{max-width:1440px;margin:10px auto 0;font-size:11px;color:#899db9}' +
    '@media(max-width:800px){.mtb-legal-wrap{grid-template-columns:1fr 1fr}.mtb-legal-wrap>div:first-child{grid-column:1/-1}}' +
    '@media(max-width:480px){.mtb-legal-footer{padding:22px 16px}.mtb-legal-wrap{grid-template-columns:1fr;gap:18px}}' +
    '.mtb-cookies{position:fixed;left:16px;right:16px;bottom:16px;z-index:9999;max-width:720px;margin:0 auto;background:#101b2b;color:#edf3fb;border:1px solid #2a3a50;border-radius:12px;padding:16px 18px;display:flex;gap:14px;align-items:center;flex-wrap:wrap;box-shadow:0 10px 30px #0008;font:13px/1.5 "Lexend","Segoe UI",sans-serif}' +
    '.mtb-cookies p{margin:0;flex:1 1 300px;color:#a6b6cc}' +
    '.mtb-cookies a{color:#32c7df}' +
    '.mtb-cookies button{background:#32c7df;color:#06242c;border:0;border-radius:8px;padding:10px 18px;font:inherit;font-weight:600;cursor:pointer}';

  function enlace(href, texto) {
    return '<li><a href="' + href + '">' + texto + '</a></li>';
  }

  function pie() {
    var f = document.createElement('footer');
    f.className = 'mtb-legal-footer';
    f.setAttribute('role', 'contentinfo');
    f.innerHTML =
      '<div class="mtb-legal-wrap">' +
        '<div><div class="mtb-legal-marca">MexTradeBot</div><div class="mtb-legal-lema">Trading agéntico</div>' +
        '<p style="margin:0;max-width:420px">Robots de trading para MetaTrader 5, análisis de mercado y formación para operar con método.</p></div>' +
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
