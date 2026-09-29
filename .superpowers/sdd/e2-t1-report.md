# E2-T1 — Mapa unico de temporalidades + alias

## Cambios
- conectividad/historico.py: `TEMPORALIDADES` (7 canonicos, llaves vela, vela_mayor, intervalo, dias, swing_length, diaria, solo_compras, es_swing), `ALIAS_TEMPORALIDAD`, `resolver_temporalidad` (ValueError si no existe). `TEMPORALIDAD_A_INTERVALO` (canonicos+alias) y `TIPOS_SOLO_ALCISTA={"Swing (S)","Swing (M)"}` derivados. `_demo_temporalidades()` offline (7 + 4 alias + invalido).
- conectividad/__init__.py: exporta los nuevos nombres.
- api/setups.py: DIAS/SWING_LENGTH/PERFIL_A_VELAS_V2/VELA_A_INTERVALO derivados del mapa (solo canonicos). `procesar` resuelve alias, responde y guarda snapshot con el canonico. Contrato EA intacto (`_forma_ea`, `setups_confirmados` ultimo; camino sin temporalidad sin cambios). Test offline en `_demo_snapshot_solo_por_defecto`: "Swing (H)" -> "Intraday 4H", snapshot guardado bajo "Intraday 4H", "Nope" -> 400.
- api/tendencia.py, backtest.py, mejor_indicador.py: resuelven alias a la entrada; default "Intraday 1H"; mejor_indicador ya no duplica DIAS (importa de setups).
- conectividad/riesgo.py: `TEMPORALIDADES_SWING={"Swing (S)","Swing (M)"}`; `es_swing` resuelve alias (asserts en demo: "Swing (H)" False, "Swing (S)" True).
- backtesting/embudo_v2.py: PERFILES = 7 canonicos. calibrar_swing_length.py: CANDIDATOS con llaves canonicas (Scalping 15m, Intraday 1H, Intraday 4H).
- api/reporte.py: solo etiquetas de la demo.

## RED/GREEN
No se hizo un RED formal previo (los asserts nuevos se escribieron junto con el codigo); los asserts nuevos fallan contra el codigo anterior por construccion (resolver_temporalidad no existia; es_swing("Swing (H)") era True).
GREEN (PYTHONIOENCODING=utf-8, .venv): historico._demo_temporalidades, conectividad.riesgo, motor_smc.setups_v2, api.reporte, refresco, api.setups (completo con red), api.tendencia, api.backtest, api.mejor_indicador: todos OK.

## Concerns
- refresco.py sigue con nombres viejos (T3): `SWING_LENGTH_POR_TEMPORALIDAD.get(c["temporalidad"])` ahora devuelve None para "Scalping"/"Intraday"/"Swing (H)" (ya no hay claves alias), asi que hasta T3 esos snapshots se verian como "vacio"; demo pasa. Tambien `desde_snapshot` y `_persistencia` con nombres viejos no coinciden hasta T4 (migracion BD).
- motor_smc.py (legacy, TIMEFRAME MT5) y persistencia/migraciones.py, poblar_snapshots.py conservan nombres viejos (T3/T4).
- `riesgo.es_swing` importa `conectividad.historico` (carga dukascopy) de forma tardia.
