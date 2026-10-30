// Barra del Master Trader (solo administrador): logo, menu, dia/noche y fondo 3D.
// Para sumar una mejora: agrega una linea a PAGINAS y su pagina en public/.
(()=>{
  const PAGINAS=[['Despacho','/despacho.html'],['Confirmaciones','/confirmaciones.html'],['Cerebro','/master.html']];
  const root=document.documentElement, aqui=location.pathname.replace(/\/$/,'');
  const guardado=k=>{try{return localStorage.getItem(k)}catch(e){return null}}, guardar=(k,v)=>{try{localStorage.setItem(k,v)}catch(e){}};

  // tema: lo elegido > preferencia del equipo
  const aplicar=t=>{root.dataset.theme=t;guardar('mtb_tema',t);const b=document.getElementById('mm-tema');if(b)b.setAttribute('aria-pressed',String(t==='night'));if(window.__pintar)window.__pintar()};
  root.dataset.theme=guardado('mtb_tema')||(matchMedia('(prefers-color-scheme: light)').matches?'day':'night');

  const sol='<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
  const luna='<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>';
  const barra=document.createElement('div');barra.className='barra';
  barra.innerHTML='<a class="marca" href="/despacho.html" aria-label="MexTradeBot, Master Trader"><span class="marca-logo"><img src="/img/logo-mtb.png" alt="" width="320" height="187"></span><span class="marca-t">Master Trader<small>Panel de administración</small></span></a>'+
    '<nav class="menu" aria-label="Master Trader">'+PAGINAS.map(([t,h])=>'<a href="'+h+'"'+(h===aqui?' aria-current="page"':'')+'>'+t+'</a>').join('')+'</nav>'+
    '<div class="herr"><button type="button" class="fondo-btn" id="mm-fondo" aria-pressed="true" title="Mostrar u ocultar el fondo 3D">Fondo 3D</button>'+
    '<button type="button" class="tema" id="mm-tema" aria-label="Cambiar entre modo día y noche" aria-pressed="true"><i></i><span class="sol">'+sol+'</span><span class="luna">'+luna+'</span></button></div>';
  const lienzo=document.createElement('canvas');lienzo.id='fondo';lienzo.setAttribute('aria-hidden','true');
  document.body.insertBefore(lienzo,document.body.firstChild);
  const main=document.querySelector('main');main.insertBefore(barra,main.firstChild);
  document.getElementById('mm-tema').addEventListener('click',()=>aplicar(root.dataset.theme==='night'?'day':'night'));
  aplicar(root.dataset.theme);
  const fb=document.getElementById('mm-fondo');
  fb.addEventListener('click',()=>{const on=fb.getAttribute('aria-pressed')!=='true';fb.setAttribute('aria-pressed',String(on));guardar('mtb_fondo',on?'1':'0');if(window.__fondoOn)window.__fondoOn(on)});
  fb.setAttribute('aria-pressed',String(guardado('mtb_fondo')!=='0'));

  // fondo 3D y pie oficial; si algo no carga, la pagina funciona igual
  const carga=src=>new Promise((ok,mal)=>{const s=document.createElement('script');s.src=src;s.onload=ok;s.onerror=mal;document.head.appendChild(s)});
  carga('https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js').then(()=>carga('/master-fondo.js?v=20261030b')).catch(()=>{lienzo.remove()});
  carga('/legal.js?v=20261030b').catch(()=>{});
})();
