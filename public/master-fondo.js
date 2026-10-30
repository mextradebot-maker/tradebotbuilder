// Fondo 3D del Master Trader: velas flotantes e indicadores simulados (solo decoracion; no lee datos del mercado).
// Lo carga master-menu.js; si Three.js no carga, la pagina funciona igual con el fondo de color.
(()=>{
  const $=id=>document.getElementById(id), root=document.documentElement, T=()=>root.dataset.theme;
  const reducir=matchMedia('(prefers-reduced-motion: reduce)').matches;
  /* ---------- escena 3D: velas flotantes formando patrones ---------- */
  const lienzo=$('fondo');
  let activo=true; window.__fondoOn=on=>{activo=on;lienzo.style.display=on?'':'none'};
  if(!window.THREE){lienzo.remove()}else{
    const R=new THREE.WebGLRenderer({canvas:lienzo,antialias:true,alpha:true});
    R.setPixelRatio(Math.min(devicePixelRatio,2));
    const E=new THREE.Scene(), C=new THREE.PerspectiveCamera(48,1,.1,200); C.position.set(0,0,24);
    const luzA=new THREE.AmbientLight(0xffffff,.5), luzD=new THREE.DirectionalLight(0xffffff,1), luzP=new THREE.PointLight(0xff5d7a,0,90);
    luzD.position.set(-12,16,14); luzP.position.set(26,-14,-4); E.add(luzA,luzD,luzP);

    const velas=[]; // x,y,z,alto,ancho,sube,fase,amplitud
    const vela=(x,y,z,h,w,sube,amp=.5)=>velas.push({x,y,z,h,w,sube,f:Math.random()*6.28,amp});
    // A. cinta de onda de mercado (lejos y grande)
    for(let i=0;i<70;i++){const x=-44+i*1.26, y=Math.sin(x*.16)*6.5-3, d=Math.cos(x*.16);vela(x,y,-30,1.6+Math.abs(d)*4.2,.62,d>0,.7)}
    // B. escalera alcista en diagonal
    for(let i=0;i<22;i++)vela(-30+i*1.5,-8+i*.95+(i%3)*.3,-12,1.4+(i%4)*.9,.55,i%5!==3,.4);
    // C. hélice vertical a la derecha
    for(let i=0;i<54;i++){const a=i*.52, y=-14+i*.55;vela(25+Math.cos(a)*6.5,y,-24+Math.sin(a)*6.5,1.5+(i%5)*.6,.55,i%3!==0,.5)}
    // D. segunda cinta, otra fase, mas al fondo
    for(let i=0;i<64;i++){const x=-48+i*1.5, y=Math.sin(x*.11+2)*8+11, d=Math.cos(x*.11+2);vela(x,y,-46,2+Math.abs(d)*5,.85,d<0,.8)}
    // E. racimos tipo "estrella de la mañana"
    [[-26,10,-8],[-14,-12,-6],[18,9,-14],[5,-3,-22],[-4,14,-30]].forEach(([x,y,z])=>{vela(x,y,z,2.2,.6,false,.6);vela(x+1.1,y-1.6,z,.9,.6,false,.6);vela(x+2.2,y+.2,z,3,.6,true,.6)});
    // F. arco descendente (corrección)
    for(let i=0;i<26;i++){const t=i/25;vela(8+t*20,12-Math.sin(t*3.14)*3-t*18,-16,1.2+(i%3)*.8,.5,i%4===0,.4)}

    let rojas=0; velas.forEach(v=>{if(!v.sube&&rojas++%3!==0)v.sube=true});
    const N=velas.length, g=new THREE.Group(); E.add(g);
    const cuerpos=new THREE.InstancedMesh(new THREE.BoxGeometry(1,1,1),new THREE.MeshStandardMaterial({roughness:.35,metalness:.15}),N);
    const mechas=new THREE.InstancedMesh(new THREE.BoxGeometry(1,1,1),new THREE.MeshBasicMaterial(),N);
    g.add(cuerpos,mechas); const o=new THREE.Object3D();
    /* ---- indicadores simulados: medias moviles, bandas, tendencia, espiral y cruces ---- */
    const mk=op=>new THREE.MeshBasicMaterial({transparent:true,opacity:op});
    const rapida=mk(.95), lenta=mk(.9), banda=mk(.38), senal=mk(1);
    const tend=mk(.85);
    const tubo=(f,x0,x1,z,r,mat)=>{const p=[];for(let x=x0;x<=x1;x+=.8)p.push(new THREE.Vector3(x,f(x),z));
      g.add(new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(p),p.length*3,r,6,false),mat))};
    const recta=(a,b)=>{const A=new THREE.Vector3(...a),B=new THREE.Vector3(...b),n=Math.round(A.distanceTo(B)/1.5);
      for(let k=0;k<n;k++){const p=A.clone().lerp(B,k/n),q=A.clone().lerp(B,(k+.62)/n);g.add(new THREE.Mesh(new THREE.TubeGeometry(new THREE.LineCurve3(p,q),1,.15,5,false),tend))}};
    const senales=[];
    [[-44,44,-29.6,x=>Math.sin((x-1.5)*.16)*6.2-3,x=>Math.sin((x-5)*.16)*5-3.6,5.4],
     [-48,48,-45.6,x=>Math.sin((x-2)*.11+2)*7.6+11,x=>Math.sin((x-7)*.11+2)*6+10.4,6.4]].forEach(([x0,x1,z,fr,le,ancho])=>{
      tubo(fr,x0,x1,z,.3,rapida); tubo(le,x0,x1,z,.3,lenta);
      tubo(x=>le(x)+ancho,x0,x1,z,.14,banda); tubo(x=>le(x)-ancho,x0,x1,z,.14,banda);   // bandas tipo Bollinger
      for(let x=x0,p=Math.sign(fr(x0)-le(x0));x<x1;x+=.25){const d=Math.sign(fr(x)-le(x));
        if(d!==p){const m=new THREE.Mesh(new THREE.SphereGeometry(.85,16,16),senal);m.position.set(x,fr(x),z);g.add(m);senales.push(m);p=d}}
    });
    recta([-33,-9.9,-11.8],[6,14.8,-11.8]);      // linea de tendencia alcista sobre la escalera
    recta([6,12.5,-15.8],[30,-6.5,-15.8]);        // resistencia descendente sobre el arco
    {const p=[];for(let i=0;i<54;i++){const a=i*.52;p.push(new THREE.Vector3(25+Math.cos(a)*6.5,-14+i*.55,-24+Math.sin(a)*6.5))}
     g.add(new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(p),200,.2,6,false),rapida))}   // espiral que cose la helice

    function pintar(){
      const noche=T()==='night';
      const up=new THREE.Color(noche?0x22e6c3:0x07937c), dn=new THREE.Color(noche?0xff5d7a:0xd03050);
      velas.forEach((v,i)=>{const c=v.sube?up:dn;cuerpos.setColorAt(i,c);mechas.setColorAt(i,c)});
      cuerpos.instanceColor.needsUpdate=mechas.instanceColor.needsUpdate=true;
      const fondo=new THREE.Color(noche?0x050912:0xe9f1f9); E.fog=new THREE.Fog(fondo,26,noche?78:70);
      luzA.intensity=noche?.38:.95; luzD.intensity=noche?1.25:.8; luzD.color.set(noche?0x7fd8ff:0xffffff);
      rapida.color.set(noche?0x4cc9ff:0x0a74c9); lenta.color.set(noche?0xffc65c:0xb86e00); banda.color.set(noche?0x9fc4ff:0x3a5a86);
      tend.color.set(noche?0xdbe9ff:0x3a5a86); senal.color.set(noche?0xffffff:0x0c1a2e);
      luzP.intensity=noche?2.2:0; mechas.material.opacity=noche?.95:.55; mechas.material.transparent=true;
    }
    function cuadro(t,mx,my){
      velas.forEach((v,i)=>{
        const y=v.y+Math.sin(t*.55+v.f)*v.amp;
        o.position.set(v.x,y,v.z); o.rotation.set(0,Math.sin(t*.2+v.f)*.35,0); o.scale.set(v.w*1.75,v.h,v.w*1.75); o.updateMatrix(); cuerpos.setMatrixAt(i,o.matrix);
        o.rotation.set(0,0,0); o.scale.set(.1,v.h+2.2,.1); o.updateMatrix(); mechas.setMatrixAt(i,o.matrix);
      });
      cuerpos.instanceMatrix.needsUpdate=mechas.instanceMatrix.needsUpdate=true;
      senales.forEach((m,i)=>m.scale.setScalar(1+.4*Math.sin(t*2.2+i*1.7)));
      g.rotation.y+=((mx*.22)-g.rotation.y)*.03; g.rotation.x+=((-my*.10)-g.rotation.x)*.03;
      C.position.x=Math.sin(t*.08)*2.2; C.lookAt(0,0,-10);
      R.render(E,C);
    }
    let mx=0,my=0,t=0,prev=performance.now(),vivo=true;
    addEventListener('pointermove',e=>{mx=e.clientX/innerWidth*2-1;my=e.clientY/innerHeight*2-1},{passive:true});
    function medir(){R.setSize(innerWidth,innerHeight,false);C.aspect=innerWidth/innerHeight;C.updateProjectionMatrix();cuadro(t,mx,my)}
    addEventListener('resize',medir); document.addEventListener('visibilitychange',()=>{vivo=!document.hidden;prev=performance.now()});
    function bucle(){requestAnimationFrame(bucle);const n=performance.now(),d=(n-prev)/1000;prev=n;if(!vivo||!activo)return;t+=d;cuadro(t,mx,my)}
    window.__pintar=pintar; pintar(); medir(); if(!reducir)bucle();
  }

  if(window.__fondoOn){let p='1';try{p=localStorage.getItem('mtb_fondo')||'1'}catch(e){}window.__fondoOn(p!=='0')}
})();
