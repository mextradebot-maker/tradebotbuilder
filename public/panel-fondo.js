// Panel de alumnos: carga el fondo de velas 3D (el mismo del Master Trader). Si no carga, el panel funciona igual.
(()=>{
  const lienzo=document.createElement('canvas');lienzo.id='fondo';lienzo.setAttribute('aria-hidden','true');document.body.insertBefore(lienzo,document.body.firstChild);
  const guardado=()=>{try{return localStorage.getItem('mtb_fondo')}catch(e){return null}};
  const b=document.createElement('button');b.type='button';b.className='mtb-fondo-btn';b.textContent='Fondo 3D';b.title='Mostrar u ocultar el fondo 3D';b.setAttribute('aria-pressed',String(guardado()!=='0'));
  b.addEventListener('click',()=>{const on=b.getAttribute('aria-pressed')!=='true';b.setAttribute('aria-pressed',String(on));try{localStorage.setItem('mtb_fondo',on?'1':'0')}catch(e){}if(window.__fondoOn)window.__fondoOn(on)});
  document.body.appendChild(b);
  const carga=src=>new Promise((ok,mal)=>{const s=document.createElement('script');s.src=src;s.onload=ok;s.onerror=mal;document.head.appendChild(s)});
  carga('https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js').then(()=>carga('/master-fondo.js?v=20261030c')).catch(()=>{lienzo.remove();b.remove()});
})();
