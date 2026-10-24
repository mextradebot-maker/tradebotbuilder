/* Pantalla «Análisis técnico» del panel de alumnos.
 * Gráfica con Lightweight Charts 5 (vendor/lightweight-charts) + barras de herramientas estilo TradingView:
 * velas cerradas de nuestro almacén (GET /api/v1/grafica), indicadores, herramientas de dibujo propias y las capas
 * que detecta nuestro motor SMC (estructura BOS/CHoCH, FVG, order blocks, liquidez).
 * Expone window.mtbAnalisisTecnico.montar(contenedor, simboloInicial). Los colores salen de los tokens --mt-* del panel. */
(function () {
  'use strict';
  var LW = window.LightweightCharts;
  var ACTIVOS = [
    ['EURUSD', 'Euro / Dólar', 'Forex'], ['GBPUSD', 'Libra / Dólar', 'Forex'], ['USDJPY', 'Dólar / Yen', 'Forex'], ['AUDUSD', 'Dólar australiano / Dólar', 'Forex'],
    ['USDCAD', 'Dólar / Dólar canadiense', 'Forex'], ['USDCHF', 'Dólar / Franco suizo', 'Forex'], ['NZDUSD', 'Dólar neozelandés / Dólar', 'Forex'],
    ['EURGBP', 'Euro / Libra', 'Forex'], ['EURJPY', 'Euro / Yen', 'Forex'], ['GBPJPY', 'Libra / Yen', 'Forex'], ['AUDJPY', 'Dólar australiano / Yen', 'Forex'],
    ['EURAUD', 'Euro / Dólar australiano', 'Forex'], ['AUDCAD', 'Dólar australiano / Dólar canadiense', 'Forex'], ['NZDJPY', 'Dólar neozelandés / Yen', 'Forex'],
    ['CADJPY', 'Dólar canadiense / Yen', 'Forex'], ['XAUUSD', 'Oro', 'Metales'], ['XAGUSD', 'Plata', 'Metales'], ['XPTUSD', 'Platino', 'Metales'],
    ['XPDUSD', 'Paladio', 'Metales'], ['US30', 'Dow Jones 30', 'Índices'], ['US100', 'Nasdaq 100', 'Índices'], ['US500', 'S&P 500', 'Índices'],
    ['GER40', 'DAX 40', 'Índices'], ['UK100', 'FTSE 100', 'Índices'], ['JP225', 'Nikkei 225', 'Índices'], ['WTIUSD', 'Petróleo WTI', 'Energía'],
    ['BRENTUSD', 'Petróleo Brent', 'Energía'], ['BTCUSD', 'Bitcoin', 'Cripto'], ['ETHUSD', 'Ethereum', 'Cripto'], ['XRPUSD', 'XRP', 'Cripto'],
    ['GOOGL', 'Alphabet / Google', 'Acciones'], ['NVDA', 'NVIDIA', 'Acciones'], ['META', 'Meta Platforms', 'Acciones'], ['WMT', 'Walmart', 'Acciones']
  ];
  var TFS = [['Scalping 15m', '15m', 900], ['Intraday 1H', '1H', 3600], ['Intraday 4H', '4H', 14400], ['Intraday D', '1D', 86400], ['Swing (S)', '1S', 604800], ['Swing (M)', '1M', 2592000]];
  var RANGOS = [['1D', 86400], ['5D', 432000], ['1M', 2592000], ['3M', 7776000], ['6M', 15552000], ['1A', 31536000], ['Todo', 0]];
  var ICONOS = {
    cursor: '<path d="M5 3l14 8-6 2-2 6z"/>', trend: '<path d="M4 19L20 5"/><circle cx="4" cy="19" r="1.6"/><circle cx="20" cy="5" r="1.6"/>',
    ray: '<path d="M4 17L21 8"/><circle cx="4" cy="17" r="1.6"/><path d="M17 6l4 2-3 4"/>', hline: '<path d="M3 12h18"/><path d="M8 9l-3 3 3 3"/>',
    vline: '<path d="M12 3v18"/><path d="M9 8l3-3 3 3"/>', rect: '<rect x="4" y="6" width="16" height="12" rx="1"/>',
    fib: '<path d="M4 5h16M4 10h16M4 14h16M4 19h16"/><path d="M6 19L18 5" stroke-dasharray="2 2"/>', measure: '<path d="M4 17l13-13 3 3L7 20z"/><path d="M9 12l2 2M12 9l2 2"/>',
    texto: '<path d="M5 6h14M12 6v13M9 19h6"/>', imán: '<path d="M6 4v8a6 6 0 0012 0V4"/><path d="M6 8h4M14 8h4"/>', borrar: '<path d="M5 7h14M9 7V5h6v2M7 7l1 13h8l1-13"/>',
    cam: '<path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>', full: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
    log: '<path d="M4 19c4 0 5-3 7-8s3-6 9-6"/>', ult: '<path d="M5 12h14M13 6l6 6-6 6"/>', ind: '<path d="M4 17l5-6 4 3 7-9"/>', smc: '<path d="M4 18V9m5 9V5m5 13v-7m5 7V8"/>',
    tipo: '<path d="M7 6v12M7 9h-2v6h2M17 4v14M17 8h-2v6h2"/>'
  };
  var PALETA = ['#32c7df', '#f0b429', '#ef5350', '#26a69a', '#ab47bc', 'texto'];
  var UP = '#26a69a', DOWN = '#ef5350';
  var HERR = [['cursor', 'Cursor'], ['trend', 'Línea de tendencia'], ['ray', 'Rayo'], ['hline', 'Línea horizontal'], ['vline', 'Línea vertical'], ['rect', 'Rectángulo'], ['fib', 'Retroceso de Fibonacci'], ['measure', 'Medir'], ['texto', 'Texto']];
  var FIB = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];
  var INDS = [['ema20', 'EMA 20', '#f0b429'], ['ema50', 'EMA 50', '#32c7df'], ['ema200', 'EMA 200', '#ab47bc'], ['sma50', 'SMA 50', '#ff9800'], ['sma200', 'SMA 200', '#90a4ae'], ['boll', 'Bandas de Bollinger (20, 2)', '#5c6bc0'], ['vol', 'Volumen', ''], ['rsi', 'RSI (14)', '#ab47bc'], ['macd', 'MACD (12, 26, 9)', '#32c7df']];
  var CAPAS = [['estructura', 'Estructura (BOS / CHoCH)'], ['fvg', 'Fair Value Gaps (FVG)'], ['order_blocks', 'Order Blocks'], ['liquidez', 'Liquidez']];
  var TIPOS = [['velas', 'Velas'], ['huecas', 'Velas huecas'], ['heikin', 'Heikin Ashi'], ['barras', 'Barras'], ['linea', 'Línea'], ['area', 'Área']];

  function $(sel, el) { return (el || document).querySelector(sel); }
  function h(tag, attrs, html) { var e = document.createElement(tag); if (attrs) for (var k in attrs) e.setAttribute(k, attrs[k]); if (html != null) e.innerHTML = html; return e; }
  function svg(n) { return '<svg viewBox="0 0 24 24" aria-hidden="true">' + (ICONOS[n] || '') + '</svg>'; }
  function guardar(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { } }
  function leer(k, d) { try { var v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } }
  function tok(n, d) { var v = getComputedStyle(E && E.root && E.root.isConnected ? E.root : document.documentElement).getPropertyValue(n).trim(); return v || d; }
  function fmt(p, dec) { return p == null ? '—' : Number(p).toFixed(dec); }
  function esc(s) { return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }

  /* ── indicadores ── */
  function sma(c, n) { var o = [], s = 0; for (var i = 0; i < c.length; i++) { s += c[i]; if (i >= n) s -= c[i - n]; if (i >= n - 1) o.push(s / n); else o.push(null); } return o; }
  function ema(c, n) { var o = [], k = 2 / (n + 1), p = null; for (var i = 0; i < c.length; i++) { if (i < n - 1) { o.push(null); continue; } if (p == null) { var s = 0; for (var j = i - n + 1; j <= i; j++) s += c[j]; p = s / n; } else p = c[i] * k + p * (1 - k); o.push(p); } return o; }
  function boll(c, n, m) { var mid = sma(c, n), up = [], lo = []; for (var i = 0; i < c.length; i++) { if (mid[i] == null) { up.push(null); lo.push(null); continue; } var v = 0; for (var j = i - n + 1; j <= i; j++) v += Math.pow(c[j] - mid[i], 2); var sd = Math.sqrt(v / n); up.push(mid[i] + m * sd); lo.push(mid[i] - m * sd); } return { mid: mid, up: up, lo: lo }; }
  function rsi(c, n) { var o = [null], g = 0, l = 0; for (var i = 1; i < c.length; i++) { var d = c[i] - c[i - 1]; var gg = Math.max(d, 0), ll = Math.max(-d, 0); if (i <= n) { g += gg; l += ll; if (i < n) { o.push(null); continue; } g /= n; l /= n; } else { g = (g * (n - 1) + gg) / n; l = (l * (n - 1) + ll) / n; } o.push(l === 0 ? 100 : 100 - 100 / (1 + g / l)); } return o; }
  function macd(c) { var a = ema(c, 12), b = ema(c, 26), m = [], i; for (i = 0; i < c.length; i++) m.push(a[i] != null && b[i] != null ? a[i] - b[i] : null); var idx = m.findIndex(function (x) { return x != null; }), sg = c.map(function () { return null; }); if (idx >= 0) { var e = ema(m.slice(idx).map(Number), 9); for (i = 0; i < e.length; i++) sg[idx + i] = e[i]; } return { m: m, s: sg, h: m.map(function (x, i) { return x != null && sg[i] != null ? x - sg[i] : null; }) }; }
  function heikin(d) { var o = [], po = null, pc = null; d.forEach(function (x) { var c = (x.o + x.h + x.l + x.c) / 4, op = po == null ? (x.o + x.c) / 2 : (po + pc) / 2; o.push({ t: x.t, o: op, h: Math.max(x.h, op, c), l: Math.min(x.l, op, c), c: c, v: x.v }); po = op; pc = c; }); return o; }
  function serie(t, arr) { var o = []; for (var i = 0; i < arr.length; i++) if (arr[i] != null) o.push({ time: t[i], value: arr[i] }); return o; }

  /* ── estado de la pantalla (una sola instancia) ── */
  var E = null;

  function cfgInicial(sim) {
    var g = leer('mtb_at_cfg', {});
    return { simbolo: sim || g.simbolo || 'EURUSD', tf: g.tf == null ? 1 : g.tf, tipo: g.tipo || 'velas', inds: g.inds || ['vol'], capas: g.capas || ['estructura'], iman: !!g.iman, log: !!g.log };
  }
  function persistirCfg() { guardar('mtb_at_cfg', { simbolo: E.simbolo, tf: E.tf, tipo: E.tipo, inds: E.inds, capas: E.capas, iman: E.iman, log: E.log }); }
  function claveDib() { return 'mtb_at_dib_' + E.simbolo + '_' + TFS[E.tf][1]; }

  /* ── tiempo <-> índice lógico (los dibujos se guardan por tiempo, no por índice) ── */
  function tiempoALogico(t) {
    var T = E.times, n = T.length; if (!n) return 0;
    if (t >= T[n - 1]) return (n - 1) + (t - T[n - 1]) / TFS[E.tf][2];
    if (t <= T[0]) return (t - T[0]) / TFS[E.tf][2];
    var lo = 0, hi = n - 1; while (hi - lo > 1) { var m = (lo + hi) >> 1; if (T[m] <= t) lo = m; else hi = m; }
    return lo + (t - T[lo]) / (T[lo + 1] - T[lo]);
  }
  function logicoATiempo(l) {
    var T = E.times, n = T.length; if (!n) return 0;
    if (l >= n - 1) return T[n - 1] + (l - (n - 1)) * TFS[E.tf][2];
    if (l <= 0) return T[0] + l * TFS[E.tf][2];
    var i = Math.floor(l); return T[i] + (l - i) * (T[i + 1] - T[i]);
  }
  function xDe(t) { var x = E.chart.timeScale().logicalToCoordinate(tiempoALogico(t)); return x == null ? null : x; }
  function yDe(p) { var y = E.main.priceToCoordinate(p); return y == null ? null : y; }
  function colorDe(c) { return c === 'texto' ? tok('--mt-text', '#edf3fb') : c; }

  /* ── primitivas: capas SMC y dibujos del usuario ── */
  function crearPrimitiva(dibujar) {
    var vista = { zOrder: function () { return 'top'; }, renderer: function () { return { draw: function (target) { target.useMediaCoordinateSpace(function (s) { try { dibujar(s.context, s.mediaSize); } catch (e) { } }); } }; } };
    var p = { _req: null, attached: function (a) { p._req = a.requestUpdate; }, detached: function () { p._req = null; }, paneViews: function () { return [vista]; }, updateAllViews: function () { }, redibujar: function () { if (p._req) p._req(); } };
    return p;
  }
  function rect(ctx, x1, y1, x2, y2, relleno, borde, dash) {
    var x = Math.min(x1, x2), y = Math.min(y1, y2), w = Math.abs(x2 - x1), hh = Math.abs(y2 - y1);
    if (relleno) { ctx.fillStyle = relleno; ctx.fillRect(x, y, w, hh); }
    if (borde) { ctx.strokeStyle = borde; ctx.lineWidth = 1; ctx.setLineDash(dash || []); ctx.strokeRect(x + 0.5, y + 0.5, w, hh); ctx.setLineDash([]); }
  }
  function etiqueta(ctx, txt, x, y, color) { ctx.font = '600 10px Lexend, sans-serif'; ctx.fillStyle = color; ctx.textBaseline = 'middle'; ctx.fillText(txt, x, y); }

  function dibujarSmc(ctx, size) {
    if (!E || !E.smc || !E.times.length) return;
    var ult = E.times[E.times.length - 1], derecha = size.width;
    var xi = function (t) { return t == null ? derecha : xDe(t); };
    E.capas.forEach(function (capa) {
      (E.smc[capa] || []).forEach(function (z) {
        var x1 = xDe(z.t), x2 = xi(z.t_fin == null ? (capa === 'estructura' ? z.t_fin : null) : z.t_fin);
        if (x1 == null) return;
        if (x2 == null) x2 = derecha;
        var alc = z.dir === 1;
        if (capa === 'fvg' || capa === 'order_blocks') {
          var y1 = yDe(z.arriba), y2 = yDe(z.abajo); if (y1 == null || y2 == null) return;
          var mit = z.t_fin != null, col = capa === 'fvg' ? (alc ? '38,166,154' : '239,83,80') : (alc ? '66,133,244' : '255,152,0');
          rect(ctx, x1, y1, x2, y2, 'rgba(' + col + ',' + (mit ? 0.07 : 0.2) + ')', 'rgba(' + col + ',' + (mit ? 0.25 : 0.7) + ')');
          if (x2 - x1 > 34) etiqueta(ctx, capa === 'fvg' ? 'FVG' : 'OB', x1 + 4, Math.min(y1, y2) + 9, 'rgb(' + col + ')');
        } else {
          var y = yDe(z.nivel); if (y == null) return;
          if (capa === 'estructura') {
            var cs = alc ? UP : DOWN; if (z.t_fin == null) x2 = derecha;
            ctx.strokeStyle = cs; ctx.lineWidth = 1; ctx.setLineDash(z.tipo === 'CHOCH' ? [2, 3] : [6, 3]); ctx.beginPath(); ctx.moveTo(x1, y + 0.5); ctx.lineTo(x2, y + 0.5); ctx.stroke(); ctx.setLineDash([]);
            etiqueta(ctx, z.tipo === 'CHOCH' ? 'CHoCH' : 'BOS', (x1 + x2) / 2 - 12, y + (alc ? -8 : 8), cs);
          } else {
            var xf = z.barrida != null ? xDe(z.barrida) : (z.t_fin != null ? xDe(z.t_fin) : derecha); if (xf == null) xf = derecha;
            ctx.strokeStyle = '#f0b429'; ctx.lineWidth = 1; ctx.setLineDash([3, 3]); ctx.beginPath(); ctx.moveTo(x1, y + 0.5); ctx.lineTo(xf, y + 0.5); ctx.stroke(); ctx.setLineDash([]);
            etiqueta(ctx, z.barrida != null ? 'Liq. barrida' : 'Liquidez', x1 + 3, y - 7, '#f0b429');
          }
        }
      });
    });
  }

  function puntosPx(d) { return d.pts.map(function (p) { return { x: xDe(p.t), y: yDe(p.p) }; }); }
  function dibujarDib(ctx, size) {
    if (!E) return;
    var todos = E.dibujos.concat(E.previa ? [E.previa] : []);
    todos.forEach(function (d) {
      var P = puntosPx(d), col = colorDe(d.color), sel = E.sel === d.id;
      if (P.some(function (p) { return p.x == null || p.y == null; })) return;
      ctx.lineWidth = sel ? 2.2 : 1.6; ctx.strokeStyle = col; ctx.fillStyle = col; ctx.setLineDash([]);
      if (d.tipo === 'trend' || d.tipo === 'ray') {
        if (P.length < 2) return; var a = P[0], b = P[1], fin = b;
        if (d.tipo === 'ray' && b.x !== a.x) { var k = (b.y - a.y) / (b.x - a.x), xe = b.x > a.x ? size.width : 0; fin = { x: xe, y: a.y + k * (xe - a.x) }; }
        ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(fin.x, fin.y); ctx.stroke();
      } else if (d.tipo === 'hline') {
        ctx.beginPath(); ctx.moveTo(0, P[0].y + 0.5); ctx.lineTo(size.width, P[0].y + 0.5); ctx.stroke();
        etiqueta(ctx, fmt(d.pts[0].p, E.dec), size.width - 58, P[0].y - 8, col);
      } else if (d.tipo === 'vline') {
        ctx.beginPath(); ctx.moveTo(P[0].x + 0.5, 0); ctx.lineTo(P[0].x + 0.5, size.height); ctx.stroke();
      } else if (d.tipo === 'rect') {
        if (P.length < 2) return; rect(ctx, P[0].x, P[0].y, P[1].x, P[1].y, colorDe(d.color) + '26', col);
      } else if (d.tipo === 'fib') {
        if (P.length < 2) return; var x1 = Math.min(P[0].x, P[1].x), x2 = Math.max(P[0].x, P[1].x), p0 = d.pts[0].p, p1 = d.pts[1].p;
        FIB.forEach(function (f, i) {
          var pr = p1 + (p0 - p1) * f, y = yDe(pr); if (y == null) return;
          if (i > 0) { var yp = yDe(p1 + (p0 - p1) * FIB[i - 1]); ctx.fillStyle = colorDe(d.color) + (i % 2 ? '12' : '1f'); ctx.fillRect(x1, Math.min(y, yp), x2 - x1, Math.abs(y - yp)); }
          ctx.strokeStyle = col; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(x1, y + 0.5); ctx.lineTo(x2, y + 0.5); ctx.stroke();
          etiqueta(ctx, f + ' (' + fmt(pr, E.dec) + ')', x1 + 4, y - 7, col);
        });
        ctx.strokeStyle = col; ctx.setLineDash([3, 3]); ctx.beginPath(); ctx.moveTo(P[0].x, P[0].y); ctx.lineTo(P[1].x, P[1].y); ctx.stroke(); ctx.setLineDash([]);
      } else if (d.tipo === 'measure') {
        if (P.length < 2) return; var dp = d.pts[1].p - d.pts[0].p, pc = dp / d.pts[0].p * 100, barras = Math.round(tiempoALogico(d.pts[1].t) - tiempoALogico(d.pts[0].t));
        var up = dp >= 0, c = up ? UP : DOWN; rect(ctx, P[0].x, P[0].y, P[1].x, P[1].y, c + '26', c);
        var tx = (up ? '+' : '') + fmt(dp, E.dec) + ' (' + (up ? '+' : '') + pc.toFixed(2) + '%)  ·  ' + barras + ' velas', w = ctx.measureText(tx).width + 16;
        ctx.font = '600 11px Lexend, sans-serif'; var bx = (P[0].x + P[1].x) / 2 - w / 2, by = Math.min(P[0].y, P[1].y) - 26; ctx.fillStyle = c; ctx.fillRect(bx, by, ctx.measureText(tx).width + 16, 20);
        ctx.fillStyle = '#fff'; ctx.textBaseline = 'middle'; ctx.fillText(tx, bx + 8, by + 10);
      } else if (d.tipo === 'texto') {
        ctx.font = '600 13px Lexend, sans-serif'; ctx.textBaseline = 'middle'; ctx.fillStyle = col; ctx.fillText(d.texto || '', P[0].x, P[0].y);
      }
      if (sel || d === E.previa) P.forEach(function (p) { ctx.beginPath(); ctx.arc(p.x, p.y, 5, 0, 7); ctx.fillStyle = tok('--mt-panel', '#101b2b'); ctx.fill(); ctx.strokeStyle = col; ctx.lineWidth = 2; ctx.stroke(); });
    });
  }

  /* ── datos ── */
  function estado(msg, spinner, reintento) {
    var el = E.estadoEl; if (!msg) { el.hidden = true; return; }
    el.hidden = false; el.innerHTML = (spinner ? '<div class="at-spin" role="status" aria-label="Cargando"></div>' : '') + '<div>' + esc(msg) + '</div>' + (reintento ? '<button type="button" class="at-btn" data-ac="reintentar" style="border-color:var(--mt-line)">Reintentar</button>' : '');
  }
  function cargar() {
    var n = ++E.reqId; estado('Cargando velas de ' + E.simbolo + '…', true);
    fetch('/api/v1/grafica?simbolo=' + encodeURIComponent(E.simbolo) + '&temporalidad=' + encodeURIComponent(TFS[E.tf][0]) + '&limite=2000', { credentials: 'same-origin' })
      .then(function (r) { if (r.status === 401) throw new Error('Inicia sesión para ver la gráfica.'); if (!r.ok) throw new Error('No se pudieron cargar las velas (' + r.status + ').'); return r.json(); })
      .then(function (d) {
        if (n !== E.reqId) return;
        if (!d.velas || !d.velas.length) { E.data = []; E.times = []; aplicar(); estado('Sin velas para ' + E.simbolo + ' en esta temporalidad.'); return; }
        E.data = d.velas; E.times = d.velas.map(function (v) { return v.t; }); E.smc = d.smc || {}; E.aviso = d.aviso || '';
        var s = String(d.velas[d.velas.length - 1].c); E.dec = Math.min(6, Math.max(2, (s.split('.')[1] || '').length));
        aplicar(); estado(''); ajustarRangoInicial();
      })
      .catch(function (e) { if (n === E.reqId) estado(e.message || 'Error de conexión.', false, true); });
  }
  function ajustarRangoInicial() { var n = E.times.length; E.chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - 140), to: n + 8 }); }

  /* ── series ── */
  function quitarSeries(lista) { lista.forEach(function (s) { try { E.chart.removeSeries(s); } catch (e) { } }); lista.length = 0; }
  function limpiarPaneles() { try { while (E.chart.panes().length > 1) E.chart.removePane(E.chart.panes().length - 1); } catch (e) { } }
  function aplicar() {
    if (E.main) { try { E.main.detachPrimitive(E.primSmc); E.main.detachPrimitive(E.primDib); } catch (e) { } try { E.chart.removeSeries(E.main); } catch (e) { } E.main = null; }
    quitarSeries(E.indSeries); limpiarPaneles();
    E.chart.applyOptions({ timeScale: { timeVisible: E.tf < 3 } });
    if (!E.data.length) { dibujar(); return; }
    var d = E.tipo === 'heikin' ? heikin(E.data) : E.data, vel = d.map(function (x) { return { time: x.t, open: x.o, high: x.h, low: x.l, close: x.c }; });
    if (E.tipo === 'linea') { E.main = E.chart.addSeries(LW.LineSeries, { color: tok('--mt-accent', '#32c7df'), lineWidth: 2 }); E.main.setData(d.map(function (x) { return { time: x.t, value: x.c }; })); }
    else if (E.tipo === 'area') { var ac = tok('--mt-accent', '#32c7df'); E.main = E.chart.addSeries(LW.AreaSeries, { lineColor: ac, topColor: ac + '66', bottomColor: ac + '05', lineWidth: 2 }); E.main.setData(d.map(function (x) { return { time: x.t, value: x.c }; })); }
    else if (E.tipo === 'barras') { E.main = E.chart.addSeries(LW.BarSeries, { upColor: UP, downColor: DOWN }); E.main.setData(vel); }
    else {
      var hueca = E.tipo === 'huecas';
      E.main = E.chart.addSeries(LW.CandlestickSeries, { upColor: hueca ? 'rgba(0,0,0,0)' : UP, downColor: DOWN, borderVisible: hueca, borderUpColor: UP, borderDownColor: DOWN, wickUpColor: UP, wickDownColor: DOWN });
      E.main.setData(vel);
    }
    E.main.applyOptions({ priceFormat: { type: 'price', precision: E.dec, minMove: Math.pow(10, -E.dec) } });
    E.main.attachPrimitive(E.primSmc); E.main.attachPrimitive(E.primDib);
    E.chart.priceScale('right').applyOptions({ mode: E.log ? LW.PriceScaleMode.Logarithmic : LW.PriceScaleMode.Normal });
    indicadores(); cargarDibujos(); leyenda(null); dibujar();
  }
  function indicadores() {
    quitarSeries(E.indSeries); limpiarPaneles();
    var T = E.times, C = E.data.map(function (x) { return x.c; }), has = function (k) { return E.inds.indexOf(k) >= 0; }, add = function (tipo, opt, pane) { var s = E.chart.addSeries(tipo, opt, pane || 0); E.indSeries.push(s); return s; };
    INDS.forEach(function (i) {
      var k = i[0]; if (!has(k)) return;
      if (/^(ema|sma)\d+$/.test(k)) { var n = parseInt(k.slice(3), 10); add(LW.LineSeries, { color: i[2], lineWidth: 1.6, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }).setData(serie(T, (k[0] === 'e' ? ema : sma)(C, n))); }
    });
    if (has('boll')) { var b = boll(C, 20, 2); [['up', b.up], ['mid', b.mid], ['lo', b.lo]].forEach(function (p) { add(LW.LineSeries, { color: '#5c6bc0', lineWidth: 1, lineStyle: p[0] === 'mid' ? 2 : 0, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }).setData(serie(T, p[1])); }); }
    if (has('vol')) {
      var v = add(LW.HistogramSeries, { priceFormat: { type: 'volume' }, priceScaleId: 'vol', priceLineVisible: false, lastValueVisible: false });
      E.chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
      v.setData(E.data.map(function (x) { return { time: x.t, value: x.v, color: x.c >= x.o ? 'rgba(38,166,154,.45)' : 'rgba(239,83,80,.45)' }; }));
    }
    var pane = 1;
    if (has('rsi')) {
      var r = add(LW.LineSeries, { color: '#ab47bc', lineWidth: 1.6, priceLineVisible: false, lastValueVisible: true }, pane);
      r.setData(serie(T, rsi(C, 14))); [30, 70].forEach(function (lv) { r.createPriceLine({ price: lv, color: tok('--mt-line', '#2a3a50'), lineStyle: 2, lineWidth: 1, axisLabelVisible: true, title: '' }); });
      pane++;
    }
    if (has('macd')) {
      var m = macd(C);
      add(LW.HistogramSeries, { priceLineVisible: false, lastValueVisible: false }, pane).setData(m.h.map(function (x, i) { return x == null ? null : { time: T[i], value: x, color: x >= 0 ? 'rgba(38,166,154,.6)' : 'rgba(239,83,80,.6)' }; }).filter(Boolean));
      add(LW.LineSeries, { color: '#32c7df', lineWidth: 1.4, priceLineVisible: false, lastValueVisible: false }, pane).setData(serie(T, m.m));
      add(LW.LineSeries, { color: '#f0b429', lineWidth: 1.4, priceLineVisible: false, lastValueVisible: false }, pane).setData(serie(T, m.s));
    }
    try { var ps = E.chart.panes(); if (ps.length > 1) { ps[0].setStretchFactor(3); for (var i = 1; i < ps.length; i++) ps[i].setStretchFactor(1); } } catch (e) { }
  }
  function dibujar() { if (E.primSmc) E.primSmc.redibujar(); if (E.primDib) E.primDib.redibujar(); }

  /* ── leyenda ── */
  function leyenda(punto) {
    var el = E.leyendaEl, a = ACTIVOS.filter(function (x) { return x[0] === E.simbolo; })[0], v = punto || E.data[E.data.length - 1], i = punto ? E.data.indexOf(punto) : E.data.length - 1, prev = i > 0 ? E.data[i - 1] : null;
    if (!v) { el.innerHTML = '<b>' + esc(E.simbolo) + '</b>'; return; }
    var dif = prev ? v.c - prev.c : 0, pc = prev ? dif / prev.c * 100 : 0, cls = dif >= 0 ? 'at-up' : 'at-down';
    el.innerHTML = '<div><b>' + esc(E.simbolo) + '</b> · ' + esc(a ? a[1] : '') + ' · ' + TFS[E.tf][1] + ' · UTC</div><div><span>A <b class="' + cls + '">' + fmt(v.o, E.dec) + '</b></span><span>Máx <b class="' + cls + '">' + fmt(v.h, E.dec) + '</b></span><span>Mín <b class="' + cls + '">' + fmt(v.l, E.dec) + '</b></span><span>C <b class="' + cls + '">' + fmt(v.c, E.dec) + '</b></span><span class="' + cls + '">' + (dif >= 0 ? '+' : '') + fmt(dif, E.dec) + ' (' + (dif >= 0 ? '+' : '') + pc.toFixed(2) + '%)</span><span>Vol <b>' + Math.round(v.v).toLocaleString('es-MX') + '</b></span></div>' + (E.aviso ? '<div>' + esc(E.aviso) + '</div>' : '');
  }

  /* ── dibujos: crear, seleccionar, mover ── */
  function cargarDibujos() { E.dibujos = leer(claveDib(), []); E.sel = null; E.previa = null; actualizarFlotante(); }
  function guardarDibujos() { guardar(claveDib(), E.dibujos); }
  function ptDe(ev) {
    var r = E.chartEl.getBoundingClientRect(), x = ev.clientX - r.left, y = ev.clientY - r.top, ps = E.chart.paneSize(0);
    if (x < 0 || y < 0 || x > ps.width || y > ps.height) return null;
    var l = E.chart.timeScale().coordinateToLogical(x), p = E.main.coordinateToPrice(y);
    if (l == null || p == null) return null;
    if (E.iman) { var i = Math.min(E.data.length - 1, Math.max(0, Math.round(l))), v = E.data[i]; if (v) { var cand = [v.o, v.h, v.l, v.c], mejor = p; cand.forEach(function (c) { var yc = yDe(c); if (yc != null && Math.abs(yc - y) < 14 && Math.abs(yc - y) < Math.abs((yDe(mejor) == null ? 1e9 : yDe(mejor)) - y) + 0.0001) mejor = c; }); p = mejor; l = i; } }
    return { t: Math.round(logicoATiempo(l)), p: p, x: x, y: y };
  }
  function distSeg(px, py, a, b) { var dx = b.x - a.x, dy = b.y - a.y, l2 = dx * dx + dy * dy, t = l2 ? Math.max(0, Math.min(1, ((px - a.x) * dx + (py - a.y) * dy) / l2)) : 0; return Math.hypot(px - (a.x + t * dx), py - (a.y + t * dy)); }
  function golpe(x, y) {
    for (var i = E.dibujos.length - 1; i >= 0; i--) {
      var d = E.dibujos[i], P = puntosPx(d); if (P.some(function (p) { return p.x == null || p.y == null; })) continue; var ok = false, w = E.chart.paneSize(0).width;
      if (d.tipo === 'trend') ok = distSeg(x, y, P[0], P[1]) < 7;
      else if (d.tipo === 'ray') { var b = P[1], a = P[0], xe = b.x >= a.x ? w : 0, k = b.x !== a.x ? (b.y - a.y) / (b.x - a.x) : 0; ok = distSeg(x, y, a, { x: xe, y: a.y + k * (xe - a.x) }) < 7; }
      else if (d.tipo === 'hline') ok = Math.abs(y - P[0].y) < 7; else if (d.tipo === 'vline') ok = Math.abs(x - P[0].x) < 7;
      else if (d.tipo === 'rect' || d.tipo === 'fib' || d.tipo === 'measure') ok = x >= Math.min(P[0].x, P[1].x) - 4 && x <= Math.max(P[0].x, P[1].x) + 4 && y >= Math.min(P[0].y, P[1].y) - 4 && y <= Math.max(P[0].y, P[1].y) + 4;
      else if (d.tipo === 'texto') ok = Math.abs(x - P[0].x - 30) < 40 && Math.abs(y - P[0].y) < 10;
      if (ok) return d;
    }
    return null;
  }
  function manejaHandle(d, x, y) { var P = puntosPx(d); for (var i = 0; i < P.length; i++) if (P[i].x != null && Math.hypot(x - P[i].x, y - P[i].y) < 9) return i; return -1; }
  function seleccionar(id) { E.sel = id; actualizarFlotante(); dibujar(); }
  function actualizarFlotante() {
    var f = E.flotante, d = E.dibujos.filter(function (x) { return x.id === E.sel; })[0];
    if (!d) { f.classList.remove('at-on'); return; }
    f.classList.add('at-on'); $$('.at-sw', f).forEach(function (b) { b.setAttribute('aria-pressed', String(b.getAttribute('data-c') === d.color)); });
  }
  function $$(s, e) { return Array.prototype.slice.call((e || document).querySelectorAll(s)); }
  function nuevoId() { return 'd' + Date.now().toString(36) + Math.random().toString(36).slice(2, 5); }
  function terminar(d) { E.dibujos.push(d); E.previa = null; guardarDibujos(); E.herr = 'cursor'; pintarHerr(); seleccionar(d.id); pista(''); }
  function pista(t) { E.pistaEl.hidden = !t; E.pistaEl.textContent = t || ''; }
  function pdown(ev) {
    if (ev.button !== 0 || !E.main || !E.data.length) return;
    var pt = ptDe(ev); if (!pt) return;
    if (E.herr !== 'cursor') {
      ev.stopPropagation(); ev.preventDefault(); var tipo = E.herr, col = E.color;
      if (tipo === 'hline' || tipo === 'vline') { terminar({ id: nuevoId(), tipo: tipo, pts: [{ t: pt.t, p: pt.p }], color: col }); return; }
      if (tipo === 'texto') { var tx = window.prompt('Texto de la nota:'); if (tx) terminar({ id: nuevoId(), tipo: 'texto', pts: [{ t: pt.t, p: pt.p }], color: col, texto: tx.slice(0, 80) }); else { E.herr = 'cursor'; pintarHerr(); } return; }
      if (!E.previa) { E.previa = { id: 'previa', tipo: tipo, pts: [{ t: pt.t, p: pt.p }, { t: pt.t, p: pt.p }], color: col }; pista('Haz clic de nuevo para terminar · Esc para cancelar'); dibujar(); }
      else { var d = E.previa; d.pts[1] = { t: pt.t, p: pt.p }; d.id = nuevoId(); terminar(d); }
      return;
    }
    var sel = E.dibujos.filter(function (x) { return x.id === E.sel; })[0];
    if (sel) { var hi = manejaHandle(sel, pt.x, pt.y); if (hi >= 0) { iniciarArrastre(ev, sel, hi, pt); return; } }
    var g = golpe(pt.x, pt.y);
    if (g) { seleccionar(g.id); iniciarArrastre(ev, g, -1, pt); } else if (E.sel) seleccionar(null);
  }
  function iniciarArrastre(ev, d, handle, pt0) {
    ev.stopPropagation(); ev.preventDefault(); E.chart.applyOptions({ handleScroll: false, handleScale: false });
    var orig = d.pts.map(function (p) { return { t: p.t, p: p.p }; }), l0 = tiempoALogico(pt0.t), mover = function (e2) {
      var p2 = ptDe(e2); if (!p2) return;
      if (handle >= 0) d.pts[handle] = { t: p2.t, p: p2.p };
      else { var dl = tiempoALogico(p2.t) - l0, dp = p2.p - pt0.p; d.pts = orig.map(function (o) { return { t: Math.round(logicoATiempo(tiempoALogico(o.t) + dl)), p: o.p + dp }; }); }
      dibujar();
    }, soltar = function () { window.removeEventListener('pointermove', mover); window.removeEventListener('pointerup', soltar); E.chart.applyOptions({ handleScroll: true, handleScale: true }); guardarDibujos(); };
    window.addEventListener('pointermove', mover); window.addEventListener('pointerup', soltar);
  }
  function pmove(ev) {
    if (E.previa) { var pt = ptDe(ev); if (pt) { E.previa.pts[1] = { t: pt.t, p: pt.p }; dibujar(); } }
  }
  function teclado(ev) {
    if (!E || !E.root.isConnected || E.root.offsetParent === null) return;
    var tag = (ev.target.tagName || '').toLowerCase(); if (tag === 'input' || tag === 'select' || tag === 'textarea') return;
    if (ev.key === 'Escape') { E.previa = null; E.herr = 'cursor'; pintarHerr(); pista(''); seleccionar(null); }
    else if ((ev.key === 'Delete' || ev.key === 'Backspace') && E.sel) { ev.preventDefault(); borrarSel(); }
  }
  function borrarSel() { E.dibujos = E.dibujos.filter(function (d) { return d.id !== E.sel; }); E.sel = null; guardarDibujos(); actualizarFlotante(); dibujar(); }

  /* ── interfaz ── */
  function pintarHerr() {
    $$('.at-tools [data-herr]', E.root).forEach(function (b) { b.setAttribute('aria-pressed', String(b.getAttribute('data-herr') === E.herr)); });
    E.chartEl.style.cursor = E.herr === 'cursor' ? '' : 'crosshair'; if (E.herr === 'cursor' && !E.previa) pista(''); else if (E.herr !== 'cursor' && !E.previa) pista('Haz clic en la gráfica para colocar el primer punto');
  }
  function pintarTop() {
    $$('.at-tf .at-btn', E.root).forEach(function (b) { b.setAttribute('aria-pressed', String(+b.getAttribute('data-tf') === E.tf)); });
    E.selSim.value = E.simbolo;
    $('[data-ac=iman]', E.root).setAttribute('aria-pressed', String(E.iman)); $('[data-ac=log]', E.root).setAttribute('aria-pressed', String(E.log));
    $$('[data-ind]', E.root).forEach(function (c) { c.checked = E.inds.indexOf(c.getAttribute('data-ind')) >= 0; });
    $$('[data-capa]', E.root).forEach(function (c) { c.checked = E.capas.indexOf(c.getAttribute('data-capa')) >= 0; });
    $$('[data-tipo]', E.root).forEach(function (c) { c.checked = c.getAttribute('data-tipo') === E.tipo; });
  }
  function aplicarTema() {
    if (!E || !E.chart) return;
    var bg = tok('--mt-panel', '#101b2b'), tx = tok('--mt-muted', '#a6b6cc'), ln = tok('--mt-line', '#2a3a50');
    E.chart.applyOptions({ layout: { background: { type: 'solid', color: bg }, textColor: tx, fontFamily: 'Lexend, Segoe UI, sans-serif' }, grid: { vertLines: { color: ln + '66' }, horzLines: { color: ln + '66' } }, rightPriceScale: { borderColor: ln }, timeScale: { borderColor: ln }, crosshair: { vertLine: { color: tx, labelBackgroundColor: tok('--mt-raised', '#162337') }, horzLine: { color: tx, labelBackgroundColor: tok('--mt-raised', '#162337') } } });
    if (E.data && E.data.length) aplicar();
  }
  function pop(btn, cont) { // menú desplegable simple
    btn.addEventListener('click', function (ev) { ev.stopPropagation(); var abierto = !cont.hidden; $$('.at-pop', E.root).forEach(function (p) { p.hidden = true; }); cont.hidden = abierto; btn.setAttribute('aria-expanded', String(!abierto)); });
  }
  function captura() { try { var c = E.chart.takeScreenshot(); c.toBlob(function (b) { var a = document.createElement('a'); a.href = URL.createObjectURL(b); a.download = 'mextradebot-' + E.simbolo + '-' + TFS[E.tf][1] + '.png'; document.body.appendChild(a); a.click(); a.remove(); setTimeout(function () { URL.revokeObjectURL(a.href); }, 4000); }); } catch (e) { } }
  function rango(seg) { var n = E.times.length; if (!n) return; if (!seg) { E.chart.timeScale().fitContent(); return; } E.chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, tiempoALogico(E.times[n - 1] - seg)), to: n + 6 }); }

  function construir(cont, sim) {
    var cfg = cfgInicial(sim);
    cont.innerHTML = '';
    var root = h('div', { 'class': 'at-wrap', id: 'atWrap' });
    var grupos = {}; ACTIVOS.forEach(function (a) { (grupos[a[2]] = grupos[a[2]] || []).push(a); });
    var opts = Object.keys(grupos).map(function (g) { return '<optgroup label="' + g + '">' + grupos[g].map(function (a) { return '<option value="' + a[0] + '">' + a[0] + ' — ' + esc(a[1]) + '</option>'; }).join('') + '</optgroup>'; }).join('');
    root.innerHTML =
      '<div class="at-top" role="toolbar" aria-label="Controles de la gráfica">' +
      '<label class="sr-only" for="atSim" style="position:absolute;left:-9999px">Activo</label><select class="at-sel" id="atSim" aria-label="Activo">' + opts + '</select><span class="at-sep"></span>' +
      '<div class="at-tf" role="group" aria-label="Temporalidad">' + TFS.map(function (t, i) { return '<button type="button" class="at-btn" data-tf="' + i + '" aria-pressed="false" title="' + t[0] + '">' + t[1] + '</button>'; }).join('') + '</div><span class="at-sep"></span>' +
      '<div class="at-menu"><button type="button" class="at-btn" data-menu="tipo" aria-haspopup="true" aria-expanded="false" title="Tipo de gráfica">' + svg('tipo') + '<span>Tipo</span></button><div class="at-pop" hidden data-pop="tipo"><h4>Tipo de gráfica</h4>' + TIPOS.map(function (t) { return '<label class="at-chk"><input type="radio" name="atTipo" data-tipo="' + t[0] + '">' + t[1] + '</label>'; }).join('') + '</div></div>' +
      '<div class="at-menu"><button type="button" class="at-btn" data-menu="ind" aria-haspopup="true" aria-expanded="false" title="Indicadores">' + svg('ind') + '<span>Indicadores</span></button><div class="at-pop" hidden data-pop="ind"><h4>Indicadores</h4>' + INDS.map(function (i) { return '<label class="at-chk"><input type="checkbox" data-ind="' + i[0] + '">' + i[1] + '</label>'; }).join('') + '</div></div>' +
      '<div class="at-menu"><button type="button" class="at-btn" data-menu="smc" aria-haspopup="true" aria-expanded="false" title="Análisis SMC de MexTradeBot">' + svg('smc') + '<span>Análisis SMC</span></button><div class="at-pop" hidden data-pop="smc"><h4>Capas del motor SMC</h4>' + CAPAS.map(function (c) { return '<label class="at-chk"><input type="checkbox" data-capa="' + c[0] + '">' + c[1] + '</label>'; }).join('') + '<p class="at-aviso" style="margin:6px 8px">Detección automática sobre las velas cerradas. Informativa; no es una recomendación.</p></div></div>' +
      '<span class="at-sep"></span><button type="button" class="at-btn" data-ac="log" aria-pressed="false" title="Escala logarítmica">' + svg('log') + '</button>' +
      '<button type="button" class="at-btn" data-ac="ult" title="Ir a la última vela">' + svg('ult') + '</button>' +
      '<button type="button" class="at-btn" data-ac="cam" title="Descargar imagen">' + svg('cam') + '</button>' +
      '<button type="button" class="at-btn" data-ac="full" title="Pantalla completa">' + svg('full') + '</button></div>' +
      '<div class="at-body"><div class="at-tools" role="toolbar" aria-label="Herramientas de dibujo" aria-orientation="vertical">' +
      HERR.map(function (x) { return '<button type="button" class="at-btn" data-herr="' + x[0] + '" aria-pressed="false" title="' + x[1] + '" aria-label="' + x[1] + '">' + svg(x[0]) + '</button>'; }).join('') +
      '<span class="at-sep"></span><button type="button" class="at-btn" data-ac="iman" aria-pressed="false" title="Imán: ajusta a máximos, mínimos, aperturas y cierres" aria-label="Imán">' + svg('imán') + '</button>' +
      '<button type="button" class="at-btn" data-ac="borrartodo" title="Borrar todos los dibujos" aria-label="Borrar todos los dibujos">' + svg('borrar') + '</button></div>' +
      '<div class="at-stage"><div class="at-chart" id="atChart"></div><div class="at-legend" aria-live="off"></div><div class="at-hint" hidden></div>' +
      '<div class="at-float" role="toolbar" aria-label="Estilo del dibujo seleccionado">' + PALETA.map(function (c) { return '<button type="button" class="at-sw" data-c="' + c + '" aria-pressed="false" aria-label="Color" style="background:' + (c === 'texto' ? 'var(--mt-text)' : c) + '"></button>'; }).join('') + '<button type="button" class="at-btn" data-ac="borrarsel" title="Eliminar dibujo" aria-label="Eliminar dibujo">' + svg('borrar') + '</button></div>' +
      '<div class="at-estado" hidden></div></div></div>' +
      '<div class="at-bottom">' + RANGOS.map(function (r) { return '<button type="button" class="at-btn" data-rango="' + r[1] + '">' + r[0] + '</button>'; }).join('') + '<span style="margin-left:auto">Velas cerradas · hora UTC · datos Dukascopy</span></div>';
    cont.appendChild(root);
    E = { root: root, chartEl: $('#atChart', root), leyendaEl: $('.at-legend', root), estadoEl: $('.at-estado', root), pistaEl: $('.at-hint', root), flotante: $('.at-float', root), selSim: $('#atSim', root),
      simbolo: cfg.simbolo, tf: cfg.tf, tipo: cfg.tipo, inds: cfg.inds, capas: cfg.capas, iman: cfg.iman, log: cfg.log, herr: 'cursor', color: PALETA[0],
      data: [], times: [], smc: {}, dec: 4, aviso: '', dibujos: [], sel: null, previa: null, indSeries: [], main: null, reqId: 0 };
    E.chart = LW.createChart(E.chartEl, { autoSize: true, crosshair: { mode: LW.CrosshairMode.Normal }, localization: { locale: 'es-MX' }, rightPriceScale: { scaleMargins: { top: 0.08, bottom: 0.08 } }, timeScale: { rightOffset: 6, barSpacing: 9, minBarSpacing: 0.4 }, handleScale: { axisPressedMouseMove: true }, kineticScroll: { mouse: false, touch: true } });
    E.primSmc = crearPrimitiva(dibujarSmc); E.primDib = crearPrimitiva(dibujarDib);
    aplicarTema();
    E.chart.timeScale().subscribeVisibleLogicalRangeChange(function () { dibujar(); });
    E.chart.subscribeCrosshairMove(function (p) { if (!E.main || !E.data.length) return; var t = p.time, v = null; if (t != null) { var i = E.times.indexOf(t); v = i >= 0 ? E.data[i] : null; } leyenda(v); });
    E.chartEl.addEventListener('pointerdown', pdown, true); E.chartEl.addEventListener('pointermove', pmove);
    document.addEventListener('keydown', teclado); document.addEventListener('click', function () { $$('.at-pop', E.root).forEach(function (p) { p.hidden = true; }); });
    new MutationObserver(function () { aplicarTema(); }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    $$('[data-menu]', root).forEach(function (b) { pop(b, $('[data-pop=' + b.getAttribute('data-menu') + ']', root)); });
    $$('.at-pop', root).forEach(function (p) { p.addEventListener('click', function (ev) { ev.stopPropagation(); }); });
    root.addEventListener('click', function (ev) {
      var b = ev.target.closest('button'); if (!b) return;
      if (b.hasAttribute('data-tf')) { E.tf = +b.getAttribute('data-tf'); persistirCfg(); pintarTop(); cargar(); }
      else if (b.hasAttribute('data-herr')) { E.herr = b.getAttribute('data-herr'); E.previa = null; pintarHerr(); }
      else if (b.hasAttribute('data-rango')) rango(+b.getAttribute('data-rango'));
      else if (b.hasAttribute('data-c')) { var d = E.dibujos.filter(function (x) { return x.id === E.sel; })[0]; if (d) { d.color = b.getAttribute('data-c'); guardarDibujos(); actualizarFlotante(); dibujar(); } E.color = b.getAttribute('data-c'); }
      else switch (b.getAttribute('data-ac')) {
        case 'iman': E.iman = !E.iman; E.chart.applyOptions({ crosshair: { mode: E.iman ? LW.CrosshairMode.Magnet : LW.CrosshairMode.Normal } }); persistirCfg(); pintarTop(); break;
        case 'log': E.log = !E.log; persistirCfg(); E.chart.priceScale('right').applyOptions({ mode: E.log ? LW.PriceScaleMode.Logarithmic : LW.PriceScaleMode.Normal }); pintarTop(); dibujar(); break;
        case 'ult': E.chart.timeScale().scrollToRealTime(); ajustarRangoInicial(); break;
        case 'cam': captura(); break;
        case 'full': if (document.fullscreenElement) document.exitFullscreen(); else root.requestFullscreen && root.requestFullscreen(); break;
        case 'borrarsel': borrarSel(); break;
        case 'borrartodo': if (E.dibujos.length && window.confirm('¿Borrar todos los dibujos de ' + E.simbolo + ' ' + TFS[E.tf][1] + '?')) { E.dibujos = []; E.sel = null; guardarDibujos(); actualizarFlotante(); dibujar(); } break;
        case 'reintentar': cargar(); break;
      }
    });
    root.addEventListener('change', function (ev) {
      var t = ev.target;
      if (t.id === 'atSim') { E.simbolo = t.value; persistirCfg(); cargar(); }
      else if (t.hasAttribute('data-tipo')) { E.tipo = t.getAttribute('data-tipo'); persistirCfg(); aplicar(); }
      else if (t.hasAttribute('data-ind')) { var k = t.getAttribute('data-ind'), i = E.inds.indexOf(k); if (t.checked && i < 0) E.inds.push(k); if (!t.checked && i >= 0) E.inds.splice(i, 1); persistirCfg(); indicadores(); }
      else if (t.hasAttribute('data-capa')) { var c = t.getAttribute('data-capa'), j = E.capas.indexOf(c); if (t.checked && j < 0) E.capas.push(c); if (!t.checked && j >= 0) E.capas.splice(j, 1); persistirCfg(); dibujar(); }
    });
    pintarTop(); pintarHerr(); if (E.iman) E.chart.applyOptions({ crosshair: { mode: LW.CrosshairMode.Magnet } });
  }

  window.mtbAnalisisTecnico = {
    montar: function (cont, simbolo) {
      if (!LW) { cont.innerHTML = '<p class="ag-empty">No se pudo cargar la librería de gráficas.</p>'; return; }
      if (E && E.root.isConnected && cont.contains(E.root)) { if (simbolo && simbolo !== E.simbolo) { E.simbolo = simbolo; pintarTop(); cargar(); } else if (!E.data.length) cargar(); return; }
      construir(cont, simbolo); cargar();
    }
  };
})();
