# Checkpoint — 8 nov 2026 — Vela de Medianoche

## Estado del código

Las 9 tareas del plan (`docs/superpowers/plans/2026-11-07-vela-medianoche.md`) están completas,
cada una revisada por separado, más una revisión final de toda la rama (modelo más capaz) que
encontró y corrigió:

- **Crítico**, ya corregido: el cierre a las 7 horas nunca sobrevivía en producción — el ack de
  "orden colocada" (pendiente, aún sin llenar) marcaba la posición como abierta, y el mismo pulso
  la borraba de inmediato porque el EA todavía reportaba 0 posiciones. Ahora el reloj arranca
  cuando `pulso()` detecta de verdad el llenado.
- **6 importantes**, ya corregidos: un alumno no podía activar el robot por su cuenta (ya deriva
  `estrategia` de `temporalidad=="Diario"`); el EA seguía poniendo take profit y moviendo
  breakeven (ya no, para esta estrategia); la orden se vencía a los 5 minutos en vez de a las 9
  horas (ya usa `ventana_hasta`); el catálogo seguía mostrando candidatos con la ventana ya
  cerrada o el PDH ya roto (ya se filtran); el cierre a las 7h dependía de un interruptor de
  cuenta apagado por defecto (ya no depende de él); el PDH se mostraba redondeado a 2 decimales
  (ya muestra precisión completa).

Rama: `worktree-feature+swing-express`. Commit final: `0e9cedc`.
Todavía NO fusionada a `main` — pendiente de que Ricardo revise y apruebe.

## Laboratorio (mtb-api-preview)

El servicio de EasyPanel apunta a la rama `laboratorio` (una rama de integración distinta, con
los landings de afiliados/trader ya mezclados), no a `worktree-feature+swing-express`. El trabajo
de Vela de Medianoche se fusionó ahí sin tocar lo demás (commit de merge `71fdf06`) y ya se
redesplegó correctamente — confirmado que el frontend y el backend del laboratorio reflejan el
código nuevo.

**Importante para la próxima sesión (ya en memoria aparte,
`reference_laboratorio_rama_propia.md`):** el laboratorio sigue siempre la rama `laboratorio`,
nunca `main`. Cualquier trabajo nuevo que se quiera probar ahí hay que fusionarlo explícitamente
a `laboratorio` (igual que se hizo aquí) y volver a disparar el webhook de despliegue.

## Pendiente — bloqueado, necesita a Ricardo

1. **VPS Windows (compilador real, MetaEditor) sin acceso.** Dirección: `38.89.76.48:42014`.
   El puerto TCP responde (confirmado con una prueba directa), pero el protocolo RDP nunca
   termina de negociar (error 0x108, "no fue posible conectar ambos equipos en el tiempo
   asignado"), probablemente el servicio de Escritorio Remoto del VPS está colgado. Ricardo no
   tiene acceso al panel de su proveedor de hosting para pedir un reinicio desde ahí — necesita
   contactar a soporte del proveedor. No hay forma de mandar un reinicio por el túnel de
   Cloudflare: ese túnel solo expone el servicio del compilador (`/compilar` + 2 rutas de estado
   de solo lectura), no un canal de administración remota, y agregar uno nuevo tendría el mismo
   problema circular (necesitaríamos entrar para desplegarlo).
2. Una vez que Ricardo pueda entrar al VPS, el comando es:
   ```
   cd C:\MTB
   git fetch origin laboratorio
   git checkout laboratorio
   ```
   Con eso el compilador (`compilador.py`) y el EA (`MexTradeBot_SeguidorSMC.mq5`) quedan con el
   soporte de "Diario" que falta ahora mismo (confirmado con una prueba directa contra
   `https://demo-status.mextradebot.com.mx/compilar`: hoy responde `400 temporalidad debe ser una
   de [... sin "Diario" en la lista]`).
3. Con el VPS actualizado, falta repetir la Tarea 9 Paso 4 (descarga real de un robot
   `vela_medianoche` desde la ficha, confirmar que llega un `.ex5` válido) y el Paso 5
   (confirmar visualmente el Catálogo de Estrategias con candidatos reales, idealmente en una
   ventana horaria donde sí haya alguno — la ventana de esta estrategia es de 00:00 a 09:00 hora
   Barcelona).
4. El EA (`MexTradeBot_SeguidorSMC.mq5`) sigue sin compilarse ni probarse en una cuenta demo real
   de MT5 — precondición conocida desde antes de empezar este trabajo, independiente de todo lo
   de arriba.
5. Fusión a `main`: pendiente de la aprobación explícita de Ricardo, después de que la prueba
   real de descarga salga bien.

## Expectativas de funcionamiento del "nuevo sistema" (Vela de Medianoche)

Regla exacta (ver `docs/superpowers/specs/2026-11-07-vela-medianoche-design.md` §2):

1. A las **00:00 hora de Barcelona**, para cada uno de los **34 activos** del catálogo, se calcula
   el PDH (máximo de las velas H1 del día anterior, si tuvo al menos 12 velas).
2. Si el precio ya rompió ese nivel al abrir el día (o si ya pasaron las 9 horas de ventana), el
   activo NO aparece como candidato — el catálogo solo muestra activos realmente operables en ese
   momento.
3. Para cada candidato vigente, el robot coloca una **orden BUY STOP** en el PDH, válida hasta las
   **09:00 hora de Barcelona** (9 horas desde medianoche). Si no se llena en ese tiempo, se cancela
   sola.
4. Si la orden se llena, la posición se cierra automáticamente **7 horas después del llenado
   real** (no 7 horas desde que se colocó la orden).
5. **Sin take profit, sin breakeven, sin trailing** — el único control de riesgo es un stop de
   emergencia al **2%** bajo el precio de entrada.
6. Esta estrategia **nunca muestra "Expectativa" ni "Winrate"** — no aplica, es un patrón mecánico,
   no una estrategia con historial de acierto evaluado.
7. **Cuentas reales habilitadas desde el día uno** — no hay restricción de solo-demo.
8. Cualquier alumno puede **descargar su propio robot desde la ficha de descarga**, eligiendo el
   símbolo (la temporalidad siempre queda fija en "Diario", el alumno no la elige). El cupo de
   descargas se comparte con Estructural y Swing Express, sin límites nuevos.
9. El `.ex5` que recibe el alumno es el mismo archivo (`MexTradeBot_SeguidorSMC.mq5`) que ya usan
   Estructural y Swing Express — no es un robot aparte. Internamente decide su comportamiento según
   el campo `estrategia`/`InpTemporalidad == "Diario"` que trae el preset.
10. El robot standalone que existía antes (`MexTradeBot_OroMedianoche.mq5`, corriendo manual en la
    demo de Ricardo desde el 1 nov) **nunca se subió a git ni se usa** — su lógica quedó portada
    dentro del sistema nuevo, y ese archivo queda obsoleto en cuanto esto se apruebe y despliegue.
