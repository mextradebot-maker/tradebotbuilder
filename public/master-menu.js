// Menu del Master Trader (solo administrador). Para sumar una mejora: agrega una linea aqui y su pagina en public/.
(()=>{
  const PAGINAS=[['Despacho','/despacho.html'],['Confirmaciones','/confirmaciones.html']];
  const aqui=location.pathname.replace(/\/$/,'');
  const st=document.createElement('style');
  st.textContent='.menu{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 16px;padding-bottom:12px;border-bottom:1px solid var(--line)}.menu a{display:inline-flex;align-items:center;min-height:44px;padding:0 16px;border-radius:8px;border:1px solid var(--line);background:var(--raised);color:var(--text);text-decoration:none;font-size:14px;transition:border-color 140ms}.menu a[aria-current=page]{background:var(--accent);color:var(--ink);border-color:var(--accent);font-weight:600}@media (hover:hover){.menu a:hover{border-color:var(--accent)}}.menu a:focus-visible{outline:2px solid var(--accent);outline-offset:2px}';
  document.head.appendChild(st);
  const nav=document.createElement('nav');nav.className='menu';nav.setAttribute('aria-label','Master Trader');
  PAGINAS.forEach(([t,h])=>{const a=document.createElement('a');a.href=h;a.textContent=t;if(h===aqui)a.setAttribute('aria-current','page');nav.appendChild(a)});
  const m=document.querySelector('main');m.insertBefore(nav,m.firstChild);
})();
