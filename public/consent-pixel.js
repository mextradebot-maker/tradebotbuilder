/* Consentimiento de cookies de marketing + Pixel de Meta. Solo para páginas públicas de venta (robots, planes, gracias).
   El píxel NO se carga hasta que la persona pulsa "Aceptar". Sin dependencias. */
(function () {
  var PIXEL_ID = '2011274582870633';
  var CLAVE = 'mtb_mkt_consent'; // 'granted' | 'denied'

  function leer() { try { return localStorage.getItem(CLAVE); } catch (e) { return null; } }
  function guardar(v) { try { localStorage.setItem(CLAVE, v); } catch (e) {} }

  function cargarPixel() {
    if (window.fbq) return;
    /* Código base oficial de Meta Pixel */
    !function (f, b, e, v, n, t, s) {
      if (f.fbq) return; n = f.fbq = function () { n.callMethod ? n.callMethod.apply(n, arguments) : n.queue.push(arguments); };
      if (!f._fbq) f._fbq = n; n.push = n; n.loaded = !0; n.version = '2.0'; n.queue = [];
      t = b.createElement(e); t.async = !0; t.src = v; s = b.getElementsByTagName(e)[0]; s.parentNode.insertBefore(t, s);
    }(window, document, 'script', 'https://connect.facebook.net/en_US/fbevents.js');
    fbq('init', PIXEL_ID);
    fbq('track', 'PageView');
  }

  /* Evento de inicio de compra al pulsar un botón de checkout de Hotmart (solo si hay consentimiento). */
  document.addEventListener('click', function (ev) {
    var a = ev.target.closest && ev.target.closest('a[href*="pay.hotmart.com"]');
    if (a && window.fbq && leer() === 'granted') fbq('track', 'InitiateCheckout');
  });

  function estilos() {
    var s = document.createElement('style');
    s.textContent = '.mtb-mkt{position:fixed;left:16px;right:16px;bottom:16px;z-index:9999;max-width:720px;margin:0 auto;background:#101b2b;color:#edf3fb;border:1px solid #2a3a50;border-radius:12px;padding:16px 18px;display:flex;gap:14px;align-items:center;flex-wrap:wrap;box-shadow:0 10px 30px #0008;font:13px/1.5 "Lexend","Segoe UI",sans-serif}' +
      '.mtb-mkt p{margin:0;flex:1 1 300px;color:#a6b6cc}.mtb-mkt a{color:#32c7df}' +
      '.mtb-mkt .b{display:flex;gap:8px}.mtb-mkt button{border:1px solid #2a3a50;background:transparent;color:#edf3fb;border-radius:8px;padding:10px 16px;min-height:44px;font:inherit;font-weight:600;cursor:pointer}' +
      '.mtb-mkt button.ok{background:#32c7df;border-color:#32c7df;color:#06242c}.mtb-mkt button:focus-visible{outline:2px solid #32c7df;outline-offset:3px}';
    document.head.appendChild(s);
  }

  function aviso() {
    if (document.querySelector('.mtb-mkt')) return;
    estilos();
    var d = document.createElement('div');
    d.className = 'mtb-mkt'; d.setAttribute('role', 'dialog'); d.setAttribute('aria-label', 'Preferencias de cookies');
    d.innerHTML = '<p>Usamos cookies de marketing de Meta (Facebook e Instagram) para medir qué páginas funcionan y mostrarte anuncios más relevantes. Solo se activan si las aceptas. ' +
      'Más detalle en la <a href="/politica-de-cookies.html">política de cookies</a>.</p>' +
      '<div class="b"><button type="button" data-v="denied">Rechazar</button><button type="button" class="ok" data-v="granted">Aceptar</button></div>';
    d.addEventListener('click', function (e) {
      var v = e.target.getAttribute && e.target.getAttribute('data-v');
      if (!v) return;
      guardar(v); d.remove();
      if (v === 'granted') cargarPixel();
    });
    document.body.appendChild(d);
  }

  /* Enlace "Preferencias de cookies" del pie: borra la decisión y vuelve a preguntar. */
  window.mtbPreferenciasCookies = function () {
    try { localStorage.removeItem(CLAVE); } catch (e) {}
    aviso();
    return false;
  };

  function iniciar() {
    var c = leer();
    if (c === 'granted') cargarPixel();
    else if (c !== 'denied') aviso();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', iniciar); else iniciar();
})();
