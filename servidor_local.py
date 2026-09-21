"""Servidor HTTP local para T-05 (Monitor Demo MT5) — backend real, Paso T-05.

Expone GET /demo-status con el resumen semanal de P&L + drawdown de las 5
cuentas demo del Master Trader. El nodo "VPS Demo Status" de T-05 en n8n lo
llama cada 6h. La evaluación de negocio la sigue haciendo Claude en n8n.

Credenciales: la cuenta 1 usa XM_LOGIN/XM_PASSWORD/XM_SERVER (igual que antes).
Las cuentas 2-5 usan XM_LOGIN_2..5 / XM_PASSWORD_2..5 / XM_SERVER_2..5.
Las que no tengan credenciales configuradas se reportan con "error": "no configurada".

Usa: python servidor_local.py   (puerto 8765, override con PORT_DEMO_STATUS)
"""

import json
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import MetaTrader5 as mt5
from dotenv import load_dotenv

from conectividad.xm import ConexionXMError, conectar, desconectar, historial_operaciones, info_cuenta, login_cuenta
from persistencia.conexion import get_conn

load_dotenv()

RUTA_DEMO_STATUS = "/demo-status"
RUTA_TRADING_RESUMEN = "/trading-resumen"
RUTA_PANEL_RESUMEN = "/panel-resumen-page"
RUTA_PANEL_RESUMEN_JSON = "/panel-resumen-json"
PUERTO_DEFAULT = 8765

_HTML_TEMPLATE = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Panel de Alumnos — MexTradeBot</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Sora:wght@500;600;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root{--bg:#0A0E1A;--surface:#121828;--surface-2:#1A2236;--surface-3:#212B44;--border:#26304A;--text:#E8ECF6;--text-muted:#8D97B3;--accent:#3B82F6;--accent-strong:#5B9CFF;--accent-soft:rgba(59,130,246,0.16);--profit:#22C55E;--profit-soft:rgba(34,197,94,0.14);--loss:#F0524A;--loss-soft:rgba(240,82,74,0.14);--warning:#E5A03C;--shadow:0 12px 28px rgba(2,6,16,0.45);}
@media(prefers-color-scheme:light){:root:not([data-theme=dark]){--bg:#F2F4F9;--surface:#fff;--surface-2:#EAEEF6;--surface-3:#DFE5F0;--border:#D8DEEC;--text:#131A2C;--text-muted:#5B6580;--accent:#2563EB;--accent-strong:#1D4ED8;--accent-soft:rgba(37,99,235,0.10);--profit:#158A46;--profit-soft:rgba(21,138,70,0.10);--loss:#C8362F;--loss-soft:rgba(200,54,47,0.10);--shadow:0 10px 24px rgba(20,30,60,0.08);}}
:root[data-theme=dark]{--bg:#0A0E1A;--surface:#121828;--surface-2:#1A2236;--surface-3:#212B44;--border:#26304A;--text:#E8ECF6;--text-muted:#8D97B3;--accent:#3B82F6;--accent-strong:#5B9CFF;--accent-soft:rgba(59,130,246,0.16);--profit:#22C55E;--profit-soft:rgba(34,197,94,0.14);--loss:#F0524A;--loss-soft:rgba(240,82,74,0.14);}
*{box-sizing:border-box;}body{margin:0;background:var(--bg);color:var(--text);font-family:"IBM Plex Sans",system-ui,sans-serif;font-size:14px;line-height:1.5;}
h1,h2,h3,.brand{font-family:"Sora",system-ui,sans-serif;}.num,td.num,.kpi-value{font-family:"IBM Plex Mono",monospace;font-variant-numeric:tabular-nums;}
a{color:var(--accent-strong);}::selection{background:var(--accent-soft);}:focus-visible{outline:none;box-shadow:0 0 0 3px rgba(91,156,255,0.45);border-radius:6px;}
.app{display:grid;grid-template-columns:236px 1fr;min-height:100vh;}
.sidebar{background:var(--surface);border-right:1px solid var(--border);padding:20px 14px;display:flex;flex-direction:column;gap:22px;}
.brand{display:flex;align-items:center;gap:10px;padding:0 6px;}.brand-mark{width:32px;height:32px;border-radius:8px;background:linear-gradient(155deg,var(--accent),var(--accent-strong));display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:13px;flex:none;}
.brand-text{font-size:14px;font-weight:600;line-height:1.25;}.brand-text small{display:block;font-family:"IBM Plex Sans";font-weight:400;font-size:11px;color:var(--text-muted);}
nav{display:flex;flex-direction:column;gap:2px;}.nav-btn{display:flex;align-items:center;gap:10px;padding:9px 10px;border-radius:8px;color:var(--text-muted);background:transparent;border:none;text-align:left;cursor:pointer;transition:background 160ms,color 160ms;font-size:13.5px;font-weight:500;width:100%;}.nav-btn:hover{background:var(--surface-2);color:var(--text);}.nav-btn[aria-current=true]{background:var(--accent-soft);color:var(--accent-strong);}
.sidebar-foot{margin-top:auto;border-top:1px solid var(--border);padding-top:14px;}
.xm-pill{display:flex;align-items:center;gap:8px;padding:8px 10px;border-radius:8px;background:var(--profit-soft);color:var(--profit);font-size:12px;font-weight:600;}.xm-pill .dot{width:7px;height:7px;border-radius:50%;background:var(--profit);flex:none;}.xm-pill.offline{background:var(--loss-soft);color:var(--loss);}.xm-pill.offline .dot{background:var(--loss);}
main{display:flex;flex-direction:column;min-width:0;}.topbar{display:flex;align-items:center;justify-content:space-between;padding:16px 28px;border-bottom:1px solid var(--border);background:var(--surface);}.topbar h1{font-size:18px;font-weight:600;margin:0;}.topbar p{margin:2px 0 0;color:var(--text-muted);font-size:12.5px;}
.student{display:flex;align-items:center;gap:10px;}.avatar{width:34px;height:34px;border-radius:50%;background:var(--surface-3);display:flex;align-items:center;justify-content:center;font-size:12.5px;font-weight:600;flex:none;}
.content{padding:26px 28px 60px;max-width:1180px;}[hidden]{display:none!important;}
.kpi-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:22px;}
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:16px 18px;}
.kpi-label{font-size:12px;color:var(--text-muted);margin-bottom:8px;}.kpi-value{font-size:22px;font-weight:600;}.kpi-value.profit{color:var(--profit);}.kpi-value.loss{color:var(--loss);}.kpi-sub{font-size:11.5px;color:var(--text-muted);margin-top:4px;}
.two-col{display:grid;grid-template-columns:1.4fr 1fr;gap:16px;}
.activity-list{display:flex;flex-direction:column;}.activity-row{display:flex;gap:12px;align-items:flex-start;padding:11px 0;border-bottom:1px solid var(--border);}.activity-row:last-child{border-bottom:none;}.activity-dot{width:8px;height:8px;border-radius:50%;margin-top:5px;flex:none;background:var(--accent);}.activity-dot.ok{background:var(--profit);}.activity-dot.warn{background:var(--warning);}.activity-dot.bad{background:var(--loss);}.activity-text{font-size:13px;}.activity-time{font-size:11.5px;color:var(--text-muted);}
.section-title{font-size:15px;font-weight:600;margin:0 0 12px;}
.table-wrap{overflow-x:auto;border:1px solid var(--border);border-radius:12px;}table{width:100%;border-collapse:collapse;font-size:13px;}th{text-align:left;font-weight:600;color:var(--text-muted);font-size:11.5px;text-transform:uppercase;letter-spacing:0.04em;padding:11px 14px;background:var(--surface-2);border-bottom:1px solid var(--border);white-space:nowrap;}td{padding:11px 14px;border-bottom:1px solid var(--border);white-space:nowrap;}tr:last-child td{border-bottom:none;}tbody tr:hover{background:var(--surface-2);}th.num,td.num{text-align:right;}
.up{color:var(--profit);}.down{color:var(--loss);}
.badge{display:inline-flex;align-items:center;gap:5px;padding:3px 9px;border-radius:99px;font-size:11.5px;font-weight:600;}.badge.buy{background:var(--profit-soft);color:var(--profit);}.badge.sell{background:var(--loss-soft);color:var(--loss);}
.update-bar{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;}.update-time{font-size:11.5px;color:var(--text-muted);}
.btn{display:inline-flex;align-items:center;gap:7px;padding:9px 15px;border-radius:8px;border:1px solid var(--border);background:var(--surface-2);color:var(--text);cursor:pointer;font-size:13px;font-weight:600;transition:transform 150ms,border-color 150ms;}.btn:hover{border-color:var(--accent);}.btn.small{padding:6px 11px;font-size:12px;}.btn:active{transform:scale(0.97);}
.empty-state{padding:24px;text-align:center;color:var(--text-muted);font-size:13px;}
@media(max-width:900px){.app{grid-template-columns:1fr;}.sidebar{flex-direction:row;overflow-x:auto;padding:10px 14px;gap:10px;}.sidebar-foot{display:none;}.brand-text small{display:none;}nav{flex-direction:row;gap:6px;}.nav-btn{white-space:nowrap;}.kpi-grid,.two-col{grid-template-columns:1fr;}}
</style>
</head>
<body>
<svg width="0" height="0" style="position:absolute"><defs>
<symbol id="i-grid" viewBox="0 0 20 20"><g fill="none" stroke="currentColor" stroke-width="1.6"><rect x="3" y="3" width="6" height="6" rx="1.2"/><rect x="11" y="3" width="6" height="6" rx="1.2"/><rect x="3" y="11" width="6" height="6" rx="1.2"/><rect x="11" y="11" width="6" height="6" rx="1.2"/></g></symbol>
<symbol id="i-chart" viewBox="0 0 20 20"><g fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M3 16V6M8 16v-4M13 16V9M17 16V3"/></g></symbol>
<symbol id="i-blocks" viewBox="0 0 20 20"><g fill="none" stroke="currentColor" stroke-width="1.6"><rect x="3" y="3" width="7" height="7" rx="1.4"/><rect x="12" y="6" width="5" height="5" rx="1.2"/><rect x="6" y="13" width="8" height="4" rx="1.2"/></g></symbol>
<symbol id="i-plug" viewBox="0 0 20 20"><g fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M7 2v4M13 2v4M5 6h10v3a5 5 0 0 1-10 0V6z"/><path d="M10 14v4"/></g></symbol>
<symbol id="i-refresh" viewBox="0 0 20 20"><g fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M16.5 4.5A8 8 0 0 0 10 2C5.58 2 2 5.58 2 10s3.58 8 8 8c3.5 0 6.5-2.24 7.59-5.36"/><path d="M16 2v5h-5"/></g></symbol>
</defs></svg>
<div class="app">
<aside class="sidebar">
<div class="brand"><div class="brand-mark">MT</div><div class="brand-text">MexTradeBot<small>Panel de Alumnos</small></div></div>
<nav id="nav">
<button class="nav-btn" data-panel="resumen" aria-current="true"><svg width="18" height="18"><use href="#i-grid"/></svg>Resumen</button>
<button class="nav-btn" data-panel="posiciones"><svg width="18" height="18"><use href="#i-chart"/></svg>Posiciones</button>
<button class="nav-btn" data-panel="historial"><svg width="18" height="18"><use href="#i-blocks"/></svg>Historial</button>
<button class="nav-btn" data-panel="xm"><svg width="18" height="18"><use href="#i-plug"/></svg>Conexion XM</button>
</nav>
<div class="sidebar-foot"><div id="xm-pill" class="xm-pill"><span class="dot"></span><span id="xm-text">Cargando...</span></div></div>
</aside>
<main>
<div class="topbar">
<div><h1 id="topbar-title">Resumen</h1><p id="topbar-sub">Tu actividad de trading en tiempo real</p></div>
<div class="student"><div><div style="font-size:13px;font-weight:600;text-align:right">MexTradeBot</div><div id="update-label" style="font-size:11px;color:var(--text-muted);text-align:right">Actualizando...</div></div><div class="avatar">MT</div></div>
</div>
<div class="content">
<section data-panel="resumen">
<div class="update-bar"><span id="updated-at" class="update-time">Cargando...</span><button class="btn small" onclick="location.reload()"><svg width="14" height="14"><use href="#i-refresh"/></svg>Actualizar</button></div>
<div class="kpi-grid">
<div class="card"><div class="kpi-label">P&amp;L del mes (demo)</div><div id="kpi-pl" class="kpi-value num">-</div><div id="kpi-pl-sub" class="kpi-sub">Calculando...</div></div>
<div class="card"><div class="kpi-label">Posiciones abiertas</div><div id="kpi-pos" class="kpi-value num">-</div><div id="kpi-pos-sub" class="kpi-sub">Cargando...</div></div>
<div class="card"><div class="kpi-label">Operaciones del mes</div><div id="kpi-ops" class="kpi-value num">-</div><div id="kpi-ops-sub" class="kpi-sub">Cargando...</div></div>
<div class="card"><div class="kpi-label">Win rate del mes</div><div id="kpi-wr" class="kpi-value num">-</div><div id="kpi-wr-sub" class="kpi-sub">Cargando...</div></div>
</div>
<div class="two-col">
<div class="card"><h2 class="section-title">Actividad reciente</h2><div id="activity-feed" class="activity-list"></div></div>
<div class="card"><h2 class="section-title">Estado de cuentas demo</h2><div id="cuentas-estado" class="activity-list"></div></div>
</div>
</section>
<section data-panel="posiciones" hidden><h2 class="section-title">Posiciones abiertas</h2><div id="tabla-pos"></div></section>
<section data-panel="historial" hidden><h2 class="section-title">Ultimas operaciones cerradas</h2><div id="tabla-hist"></div></section>
<section data-panel="xm" hidden>
<div class="card" style="max-width:540px"><h2 class="section-title">Estado de la conexion XM</h2><p style="font-size:13px;color:var(--text-muted);margin:0 0 16px">Cuentas demo configuradas en el sistema. Datos obtenidos directamente de MT5 via VPS.</p><div id="xm-det"></div></div>
</section>
</div>
</main>
</div>
<script>
var D=__DATOS_JSON__;
var datos=D||{posiciones_abiertas:[],historial_reciente:[],actualizado_en:null};
var pT={resumen:['Resumen','Tu actividad de trading en tiempo real'],posiciones:['Posiciones abiertas','Operaciones activas en MT5'],historial:['Historial','Ultimas operaciones cerradas'],xm:['Conexion XM','Estado de tus cuentas demo']};
document.querySelectorAll('.nav-btn[data-panel]').forEach(function(b){b.addEventListener('click',function(){var n=b.dataset.panel;document.querySelectorAll('main section[data-panel]').forEach(function(s){s.hidden=s.dataset.panel!==n;});document.querySelectorAll('.nav-btn').forEach(function(x){x.setAttribute('aria-current',x.dataset.panel===n?'true':'false');});var t=pT[n]||[n,''];document.getElementById('topbar-title').textContent=t[0];document.getElementById('topbar-sub').textContent=t[1];});});
function fmtF(s){if(!s)return'-';try{var d=new Date(s),h=new Date(),y=new Date(h);y.setDate(y.getDate()-1);var hr=d.toLocaleTimeString('es-MX',{hour:'2-digit',minute:'2-digit'});if(d.toDateString()===h.toDateString())return'Hoy '+hr;if(d.toDateString()===y.toDateString())return'Ayer '+hr;return d.toLocaleDateString('es-MX',{day:'numeric',month:'short'})+' '+hr;}catch(e){return s;}}
function fmtM(v){var n=parseFloat(v)||0;return(n>=0?'+':'')+' $'+Math.abs(n).toFixed(2);}
function renderizar(d){
var pos=d.posiciones_abiertas||[],hist=d.historial_reciente||[],upd=d.actualizado_en;
if(upd){document.getElementById('updated-at').textContent='Actualizado: '+fmtF(upd);document.getElementById('update-label').textContent=fmtF(upd);}
var hoy=new Date(),mes=hoy.getFullYear()+'-'+String(hoy.getMonth()+1).padStart(2,'0');
var opsMes=hist.filter(function(h){return h.cerrada_en&&h.cerrada_en.startsWith(mes);});
var plMes=opsMes.reduce(function(s,h){return s+(parseFloat(h.profit_usd)||0);},0);
var plEl=document.getElementById('kpi-pl');plEl.textContent=fmtM(plMes);plEl.className='kpi-value num '+(plMes>=0?'profit':'loss');document.getElementById('kpi-pl-sub').textContent=opsMes.length+' operacion(es) este mes';
var posEl=document.getElementById('kpi-pos');posEl.textContent=pos.length;posEl.className='kpi-value num';document.getElementById('kpi-pos-sub').textContent=pos.length>0?[...new Set(pos.map(function(p){return p.simbolo;}))].join(', '):'Sin posiciones activas';
var opsEl=document.getElementById('kpi-ops');opsEl.textContent=opsMes.length;opsEl.className='kpi-value num';var totalPl=hist.reduce(function(s,h){return s+(parseFloat(h.profit_usd)||0);},0);document.getElementById('kpi-ops-sub').textContent='Total acumulado: '+fmtM(totalPl);
var gan=opsMes.filter(function(h){return(parseFloat(h.profit_usd)||0)>0;});var wr=opsMes.length>0?Math.round(gan.length/opsMes.length*100):0;var wrEl=document.getElementById('kpi-wr');wrEl.textContent=wr+'%';wrEl.className='kpi-value num '+(wr>=50?'profit':'');document.getElementById('kpi-wr-sub').textContent=gan.length+' ganadoras / '+(opsMes.length-gan.length)+' perdedoras';
var feed=document.getElementById('activity-feed');feed.innerHTML='';
var items=[];pos.forEach(function(p){items.push({dot:'ok',txt:'Posicion abierta: <strong>'+p.simbolo+'</strong> '+p.direccion+' '+p.lotes+' lotes &mdash; '+(p.nombre||'cuenta demo'),t:fmtF(p.abierta_en)});});
hist.slice(0,Math.max(5,8-pos.length)).forEach(function(h){var pf=parseFloat(h.profit_usd)||0;items.push({dot:pf>0?'ok':pf<0?'bad':'',txt:'Op. cerrada: <strong>'+h.simbolo+'</strong> '+h.direccion+' &mdash; <strong>'+fmtM(pf)+'</strong> ('+( h.razon_cierre||'-')+')',t:fmtF(h.cerrada_en)});});
if(!items.length)feed.innerHTML='<div class="activity-row"><span class="activity-dot warn"></span><div><div class="activity-text">Sin actividad reciente</div><div class="activity-time">El coordinador no ha abierto posiciones aun</div></div></div>';
else items.forEach(function(i){feed.innerHTML+='<div class="activity-row"><span class="activity-dot '+i.dot+'"></span><div><div class="activity-text">'+i.txt+'</div><div class="activity-time">'+i.t+'</div></div></div>';});
var ce=document.getElementById('cuentas-estado');ce.innerHTML='';var cm={};pos.forEach(function(p){if(!cm[p.login])cm[p.login]={nombre:p.nombre,login:p.login,n:0,simbolos:[]};cm[p.login].n++;cm[p.login].simbolos.push(p.simbolo);});var cuentas=Object.values(cm);
if(!cuentas.length)ce.innerHTML='<div class="activity-row"><span class="activity-dot warn"></span><div><div class="activity-text">Sin posiciones abiertas en cuentas demo</div><div class="activity-time">El motor SMC aun no detecta senales suficientes</div></div></div>';
else cuentas.forEach(function(c){ce.innerHTML+='<div class="activity-row"><span class="activity-dot ok"></span><div><div class="activity-text"><strong>'+(c.nombre||'Cuenta #'+c.login)+'</strong> &mdash; '+c.n+' pos: '+c.simbolos.join(', ')+'</div><div class="activity-time">Login: '+c.login+'</div></div></div>';});
var tp=document.getElementById('tabla-pos');
if(!pos.length)tp.innerHTML='<div class="empty-state">No hay posiciones abiertas actualmente.</div>';
else{var rows=pos.map(function(p){return'<tr><td>'+(p.nombre||'-')+'</td><td class="num"><strong>'+p.simbolo+'</strong></td><td><span class="badge '+(p.direccion==='BUY'?'buy':'sell')+'">'+p.direccion+'</span></td><td class="num">'+p.lotes+'</td><td class="num">'+(parseFloat(p.precio_entrada)||'-')+'</td><td>'+p.temporalidad+'</td><td>'+fmtF(p.abierta_en)+'</td></tr>';}).join('');tp.innerHTML='<div class="table-wrap"><table><thead><tr><th>Cuenta</th><th>Simbolo</th><th>Dir.</th><th class="num">Lotes</th><th class="num">Entrada</th><th>Temporalidad</th><th>Abierta</th></tr></thead><tbody>'+rows+'</tbody></table></div>';}
var th=document.getElementById('tabla-hist');
if(!hist.length)th.innerHTML='<div class="empty-state">Sin operaciones cerradas aun.</div>';
else{var rowsH=hist.map(function(h){var pf=parseFloat(h.profit_usd)||0;return'<tr><td>'+(h.nombre||'-')+'</td><td class="num"><strong>'+h.simbolo+'</strong></td><td><span class="badge '+((h.direccion||'').toUpperCase()==='BUY'?'buy':'sell')+'">'+h.direccion+'</span></td><td class="num">'+h.lotes+'</td><td class="num '+(pf>0?'up':pf<0?'down':'')+'" ><strong>'+fmtM(pf)+'</strong></td><td>'+(h.razon_cierre||'-')+'</td><td>'+fmtF(h.cerrada_en)+'</td></tr>';}).join('');th.innerHTML='<div class="table-wrap"><table><thead><tr><th>Cuenta</th><th>Simbolo</th><th>Dir.</th><th class="num">Lotes</th><th class="num">P&amp;L</th><th>Razon cierre</th><th>Cerrada</th></tr></thead><tbody>'+rowsH+'</tbody></table></div>';}
var pill=document.getElementById('xm-pill'),pt=document.getElementById('xm-text');
if(pos.length){pill.className='xm-pill';pt.textContent='XM activa '+pos.length+' pos.';}else{pill.className='xm-pill offline';pt.textContent='Sin posiciones abiertas';}
var xd=document.getElementById('xm-det');
if(cuentas.length)xd.innerHTML=cuentas.map(function(c){return'<div style="display:flex;justify-content:space-between;padding:9px 0;border-bottom:1px solid var(--border);font-size:13px"><span style="color:var(--text-muted)">'+(c.nombre||'Cuenta')+'</span><span class="num">Login '+c.login+' &mdash; '+c.n+' pos.</span></div>';}).join('')+'<p style="font-size:11.5px;color:var(--text-muted);margin:12px 0 0">Posiciones abiertas por coordinador autonomo VPS 38.89.76.48</p>';
else xd.innerHTML='<div style="font-size:13px;color:var(--text-muted);padding:12px 0">El coordinador no tiene posiciones abiertas en este momento.</div>';
}
renderizar(datos);
</script>
</body>
</html>"""


def _cuentas_demo() -> list[dict]:
    """Lee hasta 5 slots de credenciales desde .env (sufijos "", "_2" … "_5").

    Devuelve lista de dicts con login/password/server; omite slots sin XM_LOGIN*.
    """
    cuentas = []
    for sufijo in ("", "_2", "_3", "_4", "_5"):
        login = os.environ.get(f"XM_LOGIN{sufijo}")
        pw = os.environ.get(f"XM_PASSWORD{sufijo}")
        srv = os.environ.get(f"XM_SERVER{sufijo}")
        if login and pw and srv:
            cuentas.append({"login": int(login), "password": pw, "server": srv})
    return cuentas


def calcular_resumen(cuenta: dict, deals: list[dict]) -> dict:
    """Agrega deals de cierre en P&L semanal (ISO) + drawdown máximo, en Python puro."""
    cierres = sorted(
        (d for d in deals if d.get("entry") == mt5.DEAL_ENTRY_OUT and d.get("profit") is not None),
        key=lambda d: d["time"],
    )

    semanas: dict[str, dict] = {}
    curva_acumulada = []
    acumulado = 0.0
    for d in cierres:
        neto = d["profit"] + d.get("swap", 0.0) + d.get("commission", 0.0)
        acumulado += neto
        curva_acumulada.append(acumulado)
        semana = datetime.fromtimestamp(d["time"], tz=timezone.utc).strftime("%G-W%V")
        bucket = semanas.setdefault(semana, {"semana": semana, "profit": 0.0, "operaciones": 0})
        bucket["profit"] += neto
        bucket["operaciones"] += 1

    # ponytail: asume sin depósitos/retiros manuales durante la ventana (razonable
    # para una demo dedicada a probar el EA) — si Ricardo deposita/retira a mano,
    # esto desvía el cálculo; agregar filtro por DEAL_TYPE_BALANCE si eso empieza a pasar.
    balance_inicial = cuenta["balance"] - acumulado
    pico = balance_inicial
    drawdown_max = 0.0
    for valor in curva_acumulada:
        saldo = balance_inicial + valor
        pico = max(pico, saldo)
        if pico > 0:
            drawdown_max = max(drawdown_max, (pico - saldo) / pico)

    return {
        "cuenta": {k: cuenta[k] for k in ("login", "server", "balance", "equity", "currency") if k in cuenta},
        "semanas": [semanas[k] for k in sorted(semanas)],
        "drawdown_pct": round(drawdown_max * 100, 2),
        "num_operaciones": len(cierres),
    }


def resumen_todas(dias: int = 60) -> dict:
    """Reporta las N cuentas demo configuradas en .env.

    Si una cuenta falla (credenciales incorrectas, servidor caído), la incluye
    con "error": "..." en vez de abortar el request completo.
    """
    cuentas = _cuentas_demo()
    if not cuentas:
        raise ConexionXMError("No hay cuentas configuradas (verifica XM_LOGIN en .env)")

    conectar()
    try:
        resultados = []
        for c in cuentas:
            try:
                login_cuenta(c["login"], c["password"], c["server"])
                resultados.append(calcular_resumen(info_cuenta(), historial_operaciones(dias=dias)))
            except ConexionXMError as e:
                resultados.append({"cuenta": {"login": c["login"], "server": c["server"]}, "error": str(e)})
        return {"cuentas": resultados}
    finally:
        desconectar()


def trading_resumen() -> dict:
    """Posiciones abiertas + últimas 10 operaciones cerradas desde Postgres."""
    with get_conn() as conn:
        pos_rows = conn.execute(
            """SELECT p.login, cd.nombre, p.simbolo, p.temporalidad, p.direccion,
                      p.lotes, p.precio_entrada, p.abierta_en
               FROM posiciones_abiertas p
               JOIN cuentas_demo cd ON cd.login = p.login
               ORDER BY p.abierta_en DESC"""
        ).fetchall()
        hist_rows = conn.execute(
            """SELECT h.simbolo, cd.nombre, h.direccion, h.lotes,
                      h.profit_usd, h.razon_cierre, h.cerrada_en
               FROM historial_posiciones h
               JOIN cuentas_demo cd ON cd.login = h.login
               ORDER BY h.cerrada_en DESC LIMIT 10"""
        ).fetchall()

    def _fmt(row, keys):
        d = dict(zip(keys, row))
        for k in d:
            v = d[k]
            if hasattr(v, "isoformat"):
                d[k] = v.isoformat()
            elif hasattr(v, "__float__"):
                d[k] = float(v)
        return d

    pos_keys = ("login", "nombre", "simbolo", "temporalidad", "direccion", "lotes", "precio_entrada", "abierta_en")
    hist_keys = ("simbolo", "nombre", "direccion", "lotes", "profit_usd", "razon_cierre", "cerrada_en")

    return {
        "posiciones_abiertas": [_fmt(r, pos_keys) for r in pos_rows],
        "historial_reciente": [_fmt(r, hist_keys) for r in hist_rows],
        "actualizado_en": datetime.now(tz=timezone.utc).isoformat(),
    }


def panel_resumen_page() -> str:
    data = trading_resumen()
    return _HTML_TEMPLATE.replace("__DATOS_JSON__", json.dumps(data))


def procesar(qs: dict) -> tuple[int, dict]:
    try:
        dias = int(qs.get("dias", 60))
    except ValueError:
        return 400, {"error": "'dias' debe ser un entero"}
    try:
        return 200, resumen_todas(dias=dias)
    except ConexionXMError as e:
        return 502, {"error": str(e)}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        partes = urlparse(self.path)
        ruta = partes.path.rstrip("/")
        if ruta == RUTA_DEMO_STATUS:
            qs = {k: v[0] for k, v in parse_qs(partes.query).items()}
            status, body = procesar(qs)
        elif ruta == RUTA_TRADING_RESUMEN:
            try:
                status, body = 200, trading_resumen()
            except Exception as e:
                status, body = 502, {"error": str(e)}
            self._responder(status, body)
            return
        elif ruta == RUTA_PANEL_RESUMEN:
            try:
                html = panel_resumen_page()
                self._responder_html(200, html)
            except Exception as e:
                self._responder(502, {"error": str(e)})
            return
        elif ruta == RUTA_PANEL_RESUMEN_JSON:
            try:
                html = panel_resumen_page()
                self._responder(200, {"html": html})
            except Exception as e:
                self._responder(502, {"error": str(e)})
            return
        else:
            status, body = 404, {"error": f"rutas: {RUTA_DEMO_STATUS}, {RUTA_TRADING_RESUMEN}, {RUTA_PANEL_RESUMEN}, {RUTA_PANEL_RESUMEN_JSON}"}
        self._responder(status, body)

    def _responder_html(self, status: int, html: str) -> None:
        cuerpo = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def _responder(self, status: int, payload: dict) -> None:
        cuerpo = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)


def demo() -> None:
    cuenta = {"login": 318680674, "server": "XMGlobal-MT5 7", "balance": 10500.0, "equity": 10500.0, "currency": "USD"}

    def ts(dia: int, hora: int) -> int:
        return int(datetime(2026, 8, dia, hora, tzinfo=timezone.utc).timestamp())

    deals = [
        {"time": ts(3, 10), "entry": mt5.DEAL_ENTRY_OUT, "profit": 200.0, "swap": 0.0, "commission": 0.0},
        {"time": ts(4, 15), "entry": mt5.DEAL_ENTRY_OUT, "profit": 100.0, "swap": 0.0, "commission": 0.0},
        {"time": ts(10, 10), "entry": mt5.DEAL_ENTRY_OUT, "profit": -50.0, "swap": 0.0, "commission": 0.0},
        {"time": ts(11, 15), "entry": mt5.DEAL_ENTRY_OUT, "profit": 300.0, "swap": -1.0, "commission": -2.0},
        {"time": ts(17, 10), "entry": mt5.DEAL_ENTRY_OUT, "profit": -400.0, "swap": 0.0, "commission": 0.0},
        {"time": ts(18, 15), "entry": mt5.DEAL_ENTRY_OUT, "profit": 150.0, "swap": 0.0, "commission": 0.0},
        {"time": ts(19, 9), "entry": mt5.DEAL_ENTRY_IN, "profit": 0.0, "swap": 0.0, "commission": 0.0},
    ]

    resumen = calcular_resumen(cuenta, deals)
    assert resumen["num_operaciones"] == 6, resumen
    semanas = {s["semana"]: s for s in resumen["semanas"]}
    assert len(semanas) == 3
    assert round(semanas["2026-W32"]["profit"], 2) == 300.0
    assert round(semanas["2026-W33"]["profit"], 2) == 247.0
    assert round(semanas["2026-W34"]["profit"], 2) == -250.0
    assert resumen["drawdown_pct"] == 3.72, resumen["drawdown_pct"]

    status, body = procesar({"dias": "no-es-numero"})
    assert status == 400 and "error" in body

    print(f"servidor_local.demo() OK — calcular_resumen pasa; resumen_todas requiere MT5 en vivo")


if __name__ == "__main__":
    demo()

    puerto = int(os.environ.get("PORT_DEMO_STATUS", PUERTO_DEFAULT))
    print(f"Sirviendo GET {RUTA_DEMO_STATUS} en http://localhost:{puerto} (Ctrl+C para detener)")
    HTTPServer(("0.0.0.0", puerto), Handler).serve_forever()
