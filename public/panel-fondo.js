// Panel de alumnos: fondo 3D con la imagen del oso y el toro (sin velas animadas y sin three.js).
// El efecto 3D es parallax: la imagen se inclina y se desplaza con el puntero sobre una perspectiva CSS, y "respira" muy poco en reposo.
// Rendimiento: la imagen solo se pide cuando el fondo esta encendido, y por defecto arranca apagado en pantallas chicas,
// con "reducir movimiento" o con ahorro de datos. Lo que elija el usuario con el boton (mtb_fondo = 1 / 0) siempre manda.
(()=>{
  const ANCHO_MIN=900; // ponytail: por debajo de este ancho el fondo arranca apagado (se enciende con el boton)
  const cont=document.createElement('div');cont.id='fondo-img';cont.setAttribute('aria-hidden','true');
  const capa=document.createElement('div');capa.className='capa';cont.appendChild(capa);
  document.body.insertBefore(cont,document.body.firstChild);
  const guardado=()=>{try{return localStorage.getItem('mtb_fondo')}catch(e){return null}};
  const reducido=matchMedia('(prefers-reduced-motion: reduce)');
  const liviano=()=>matchMedia('(max-width:'+(ANCHO_MIN-1)+'px)').matches||reducido.matches||!!(navigator.connection&&navigator.connection.saveData);
  const g=guardado();
  let quiere=g==='1'?true:g==='0'?false:!liviano();
  const b=document.createElement('button');b.type='button';b.className='mtb-fondo-btn';b.textContent='Fondo 3D';b.title='Mostrar u ocultar el fondo 3D';
  document.body.appendChild(b);
  let px=0,py=0,cx=0,cy=0,raf=0;
  addEventListener('pointermove',e=>{px=e.clientX/innerWidth-.5;py=e.clientY/innerHeight-.5},{passive:true});
  const paso=t=>{
    raf=0; if(!quiere||reducido.matches)return; // sin movimiento si el usuario pidio reducirlo
    if(!document.hidden){
      cx+=(px-cx)*.06; cy+=(py-cy)*.06;
      const s=capa.style;
      s.setProperty('--ry',(cx*6+Math.sin(t/5200)*.8).toFixed(3)+'deg');
      s.setProperty('--rx',(-cy*4+Math.cos(t/6100)*.6).toFixed(3)+'deg');
      s.setProperty('--tx',(-cx*28).toFixed(2)+'px');
      s.setProperty('--ty',(-cy*18).toFixed(2)+'px');
    }
    raf=requestAnimationFrame(paso);
  };
  const aplicar=()=>{
    cont.classList.toggle('on',quiere);b.setAttribute('aria-pressed',String(quiere));
    if(quiere&&!raf)raf=requestAnimationFrame(paso);
  };
  b.addEventListener('click',()=>{
    quiere=b.getAttribute('aria-pressed')!=='true';
    try{localStorage.setItem('mtb_fondo',quiere?'1':'0')}catch(e){}
    aplicar();
  });
  aplicar();
})();
