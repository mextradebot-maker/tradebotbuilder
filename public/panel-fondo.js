// Panel de alumnos: carga el fondo de velas 3D (el mismo del Master Trader). Si no carga, el panel funciona igual.
// Rendimiento: three.js (121 KB) y la animacion NO se cargan hasta que la pagina termino y el navegador esta libre,
// y por defecto no arrancan en pantallas chicas, con "reducir movimiento" o con ahorro de datos.
// Lo que elija el usuario con el boton (mtb_fondo = 1 / 0) siempre manda sobre ese valor por defecto.
(()=>{
  const ANCHO_MIN=900; // ponytail: por debajo de este ancho el fondo arranca apagado (se enciende con el boton)
  const lienzo=document.createElement('canvas');lienzo.id='fondo';lienzo.setAttribute('aria-hidden','true');document.body.insertBefore(lienzo,document.body.firstChild);
  const guardado=()=>{try{return localStorage.getItem('mtb_fondo')}catch(e){return null}};
  const liviano=()=>matchMedia('(max-width:'+(ANCHO_MIN-1)+'px)').matches||matchMedia('(prefers-reduced-motion: reduce)').matches||!!(navigator.connection&&navigator.connection.saveData);
  const g=guardado();
  let quiere=g==='1'?true:g==='0'?false:!liviano();
  let estado=0; // 0 sin cargar, 1 cargando, 2 listo
  const b=document.createElement('button');b.type='button';b.className='mtb-fondo-btn';b.textContent='Fondo 3D';b.title='Mostrar u ocultar el fondo 3D';b.setAttribute('aria-pressed',String(quiere));
  document.body.appendChild(b);
  lienzo.style.transition='opacity .8s ease';
  if(quiere)lienzo.style.opacity='0'; else lienzo.style.display='none';
  const carga=src=>new Promise((ok,mal)=>{const s=document.createElement('script');s.src=src;s.onload=ok;s.onerror=mal;document.head.appendChild(s)});
  const iniciar=()=>{
    if(estado===2){if(window.__fondoOn)window.__fondoOn(true);lienzo.style.opacity='1';return}
    if(estado===1)return; estado=1;
    carga('https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js').then(()=>carga('/master-fondo.js?v=20261030c'))
      .then(()=>{estado=2;requestAnimationFrame(()=>{lienzo.style.opacity='1'})}).catch(()=>{lienzo.remove();b.remove()});
  };
  b.addEventListener('click',()=>{
    quiere=b.getAttribute('aria-pressed')!=='true';b.setAttribute('aria-pressed',String(quiere));
    try{localStorage.setItem('mtb_fondo',quiere?'1':'0')}catch(e){}
    if(quiere){lienzo.style.display='';iniciar()}else if(window.__fondoOn)window.__fondoOn(false);else lienzo.style.display='none';
  });
  if(quiere){
    const libre=()=>('requestIdleCallback' in window)?requestIdleCallback(iniciar,{timeout:3000}):setTimeout(iniciar,1500);
    if(document.readyState==='complete')libre(); else addEventListener('load',libre,{once:true});
  }
})();
