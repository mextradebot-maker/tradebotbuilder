//+------------------------------------------------------------------+
//| MexTradeBot_SeguidorSMC.mq5                                      |
//| MexTradeBot — motor SMC propio (Order Blocks + FVG + liquidez)   |
//|                                                                    |
//| Este EA NO reimplementa la deteccion SMC en MQL5 -- en cada vela  |
//| nueva le pregunta a /api/setups (el motor Python ya validado, ver |
//| tradebotbuilder/motor_smc y backtesting/) si hay un setup activo, |
//| y opera exactamente lo que responda. Cualquier mejora al motor se |
//| refleja aqui sin tocar este archivo. Ver docs/manual-tecnico-     |
//| interno.md Seccion 7.1 para la decision Opcion A vs Opcion B.     |
//|                                                                    |
//| Opera solo setups_confirmados -- /api/setups (con InpTemporalidad) |
//| responde con el motor SMC v2: confirmado = cumple las reglas R1-R6 |
//| (ver api/setups.py). Si la API responde sin ese campo (version     |
//| vieja), este EA no opera -- mas seguro que asumir el setup crudo   |
//| sin confirmar.                                                     |
//|                                                                    |
//| RIESGO (v1.11, reglas de Ricardo 29 sep 2026): 2% por operacion   |
//| (el servidor lo topa en 2%: si ni el lote minimo cabe, no abre).  |
//| Swing (S) y Swing (M) abren SIN SL (aguantan el drawdown); su     |
//| tamano usa el stop del motor solo como referencia de distancia.   |
//| CIERRE FORZADO (v1.12): toda posicion de este EA (todas las       |
//| temporalidades) se cierra si su perdida flotante llega al 3% del  |
//| capital. En Swing S/M es el unico freno; en el resto, un seguro   |
//| contra huecos de precio/deslizamiento.                            |
//|                                                                    |
//| BREAKEVEN ESCALONADO (v1.13, regla de Ricardo 29 sep 2026): el SL |
//| va 1R detras del precio -- a 1R pasa a la entrada, a 1.5R a +0.5R,|
//| a 2R a +1R; el TP del motor no cambia. R = distancia del SL       |
//| original (se guarda en una variable global de la terminal).       |
//| TRAILING POR ESTRUCTURA (v1.15, curso L44 "cadena de demanda"):   |
//| desde 2R el SL sube a debajo de cada nuevo minimo mas alto (en    |
//| venta, arriba de cada nuevo maximo mas bajo) de InpTF, si queda   |
//| mejor que el escalon; nunca baja.                                 |
//| OJO: backtesting/backtest.py NO simula esta gestion todavia.      |
//|                                                                    |
//| AJUSTE DE PRECIO (v1.14): el motor usa el feed de Dukascopy; en   |
//| algunos simbolos el broker cotiza distinto (WTI: XM OILCash va    |
//| ~$3 arriba del futuro). La API manda el cierre de su ultima vela  |
//| 15m (ref_ts/ref_cierre); el EA compara con el cierre de SU broker |
//| en esa misma vela y recorre entrada/SL/TP por la diferencia. Si   |
//| la diferencia pasa de InpAjusteMaxPct, no opera (simbolo mal      |
//| elegido en el grafico).                                           |
//| Si el precio toca el TP antes de llenar la entrada, la orden      |
//| pendiente se cancela (igual que el backtest: "cancelado_tp").     |
//|                                                                    |
//| LICENCIA (v1.10): sin MTB_LICENSE_TOKEN valido no hay senales ni  |
//| ordenes. Cada consulta a /api/setups va firmada con el token y    |
//| antes de cada orden se pide autorizacion a /api/v1/auth, que      |
//| ademas devuelve los LOTES calculados con la regla unica del       |
//| servidor (conectividad/riesgo.py) -- este EA ya no calcula lotes. |
//| Token revocado/expirado/kill switch -> STANDBY LOCK: cancela sus  |
//| ordenes pendientes y no opera hasta que el servidor lo reautorice.|
//+------------------------------------------------------------------+
#property copyright "MexTradeBot"
#property version   "1.15"
#property strict

#define MTB_ROBOT_ID "seguidor-smc"   // id del robot en la licencia -- fijo, no editable por el cliente

//--- LICENCIA MASTER TRADER
input string MTB_LICENSE_TOKEN   = "";            // Token entregado por MexTradeBot (MTB-XXXXX-XXXXX-XXXXX-XXXXX)
input string InpAuthUrl          = "https://mextradebot.com.mx/api/v1/auth"; // Autorizacion + lotes

//--- CONEXION AL MOTOR PROPIO
input string InpApiUrl           = "https://mextradebot.com.mx/api/setups"; // URL de /api/setups
input string InpSimboloConsulta  = "XAUUSD";      // Simbolo tal como lo espera la API (ver conectividad.SIMBOLOS)
input string InpTemporalidad     = "Intraday 1H"; // Scalping 15m / Scalping 30m / Intraday 1H / Intraday 4H / Intraday D / Swing (S) / Swing (M) (nombres viejos aceptados por la API)
input int    InpDiasHistorico    = 0;             // 0 = usa el default calibrado de la API para InpTemporalidad

//--- PARAMETROS DE OPERACION
input ENUM_TIMEFRAMES InpTF      = PERIOD_H1;     // Timeframe del disparo (nueva vela = nueva consulta)
input double InpRiskPercent      = 2.0;           // Riesgo por operacion (%) -- maximo 2%, los lotes los calcula el servidor
input double InpRiskPercentSwing = 1.0;           // Swing (S)/(M): % provisional sobre la distancia del stop del motor (sin SL real)
input double InpTakeProfitR      = 2.0;           // Respaldo: TP en multiplos de R, solo si la API no manda "tp"
input int    InpVelasExpiracion  = 20;            // Velas que la orden pendiente espera antes de cancelarse
input double InpPerdidaMaxPct    = 3.0;           // Cierre forzado: perdida flotante maxima (% del capital)
input double InpAjusteMaxPct     = 5.0;           // Diferencia maxima broker vs motor (%) -- mas que esto = simbolo equivocado, no opera
input int    InpMagicNumber      = 20260828;      // Numero magico unico del EA
input string InpComment          = "MTB-SMC";     // Comentario en operaciones

//--- ESTADO INTERNO
datetime g_ultima_vela = 0;
double   g_ultima_entrada_operada = 0;
bool     g_standby = false;               // true = licencia rechazada, no opera

//+------------------------------------------------------------------+
//| INICIALIZACION                                                    |
//+------------------------------------------------------------------+
int OnInit()
{
   if(StringLen(InpApiUrl) == 0 || StringLen(InpAuthUrl) == 0)
   {
      Print("ERROR: InpApiUrl / InpAuthUrl vacio");
      return INIT_FAILED;
   }
   if(StringLen(MTB_LICENSE_TOKEN) == 0)
   {
      Print("BLOQUEO DE SEGURIDAD: falta MTB_LICENSE_TOKEN. Solicita tu token a MexTradeBot.");
      return INIT_FAILED;
   }
   ENUM_TIMEFRAMES tf_esperado;
   if(!TimeframeEsperado(InpTemporalidad, tf_esperado))
   {
      Print("ERROR: temporalidad desconocida '", InpTemporalidad, "'. Usa: Scalping 15m, Scalping 30m, Intraday 1H, Intraday 4H, Intraday D, Swing (S) o Swing (M).");
      return INIT_PARAMETERS_INCORRECT;
   }
   ENUM_TIMEFRAMES tf_grafico = (InpTF == PERIOD_CURRENT) ? (ENUM_TIMEFRAMES)Period() : InpTF;
   if(tf_esperado != tf_grafico)
   {
      Print("ERROR: la temporalidad '", InpTemporalidad, "' requiere InpTF = ", EnumToString(tf_esperado), " pero InpTF = ", EnumToString(InpTF), ". Ajusta InpTF o InpTemporalidad; el EA no inicia.");
      return INIT_PARAMETERS_INCORRECT;
   }
   Print("MexTradeBot_SeguidorSMC inicializado -- consultando ", InpApiUrl, " para ", InpSimboloConsulta, " (", InpTemporalidad, "), solo opera setups confirmados");
   Print("Licencia: cuenta ", AccountInfoInteger(ACCOUNT_LOGIN), " (", ModoCuenta(), "), robot ", MTB_ROBOT_ID);
   Print("IMPORTANTE: agrega 'https://mextradebot.com.mx' (cubre ", InpApiUrl, " y ", InpAuthUrl, ") en Herramientas > Opciones > Expert Advisors > 'Permitir WebRequest para las URL siguientes', si no las consultas fallan.");
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Timeframe que corresponde a cada temporalidad (canonicas + alias  |
//| viejos que la API sigue aceptando). false = nombre desconocido.   |
//+------------------------------------------------------------------+
bool TimeframeEsperado(string nombre, ENUM_TIMEFRAMES &tf)
{
   StringTrimLeft(nombre);
   StringTrimRight(nombre);
   if(nombre == "Scalping 15m" || nombre == "Scalping")                        tf = PERIOD_M15;
   else if(nombre == "Scalping 30m")                                            tf = PERIOD_M30;
   else if(nombre == "Intraday 1H" || nombre == "Intraday")                     tf = PERIOD_H1;
   else if(nombre == "Intraday 4H" || nombre == "Swing (H)" || nombre == "Swing") tf = PERIOD_H4;
   else if(nombre == "Intraday D")                                              tf = PERIOD_D1;
   else if(nombre == "Swing (S)")                                               tf = PERIOD_W1;
   else if(nombre == "Swing (M)")                                               tf = PERIOD_MN1;
   else return false;
   return true;
}

bool EsSwingSM()
{
   ENUM_TIMEFRAMES tf;
   return TimeframeEsperado(InpTemporalidad, tf) && (tf == PERIOD_W1 || tf == PERIOD_MN1);
}

//+------------------------------------------------------------------+
//| TICK PRINCIPAL                                                    |
//+------------------------------------------------------------------+
void OnTick()
{
   CerrarPorPerdidaMaxima(); // cada tick: el freno del -3% no espera a la vela
   MoverBreakeven();        // cada tick: el escalon se alcanza a mitad de vela
   CancelarOrdenesPorTP(); // cada tick: el TP se puede tocar a mitad de vela

   // Solo procesar en vela nueva -- evita golpear la API en cada tick
   datetime vela_actual = iTime(_Symbol, InpTF, 0);
   if(vela_actual == g_ultima_vela) return;
   g_ultima_vela = vela_actual;

   CancelarOrdenesVencidas();

   if(CountPositions() > 0 || CountOrdenesPendientes() > 0) return; // ya hay algo abierto/pendiente de este EA

   string direccion;
   double entrada, stop, tp_api, ajuste;
   if(!ConsultarUltimoSetup(direccion, entrada, stop, tp_api, ajuste)) return;
   if(g_standby) return;

   // evita re-operar exactamente el mismo setup si ya se coloco antes (precio del motor, sin ajuste)
   if(MathAbs(entrada - g_ultima_entrada_operada) < _Point) return;
   double entrada_motor = entrada;
   entrada += ajuste;
   stop    += ajuste;
   if(tp_api > 0) tp_api += ajuste;

   double riesgo = MathAbs(entrada - stop);
   if(riesgo <= 0) return; // geometria invalida, no deberia pasar (ya filtrado por detectar_setups)

   // TP del motor si viene y esta del lado correcto; si no, respaldo con InpTakeProfitR
   bool tp_api_ok = (tp_api > 0) && ((direccion == "long") ? (tp_api > entrada) : (tp_api < entrada));
   double tp = tp_api_ok ? tp_api : ((direccion == "long") ? entrada + InpTakeProfitR * riesgo : entrada - InpTakeProfitR * riesgo);
   ENUM_ORDER_TYPE tipo = (direccion == "long") ? ORDER_TYPE_BUY_LIMIT : ORDER_TYPE_SELL_LIMIT;

   double lotes;
   if(!AutorizarOrden(direccion, entrada, stop, lotes)) return;

   // Swing (S)/(M): sin SL en la orden -- la salida la gestionan las reglas de swing
   double sl_orden = EsSwingSM() ? 0.0 : stop;
   if(ColocarOrdenPendiente(tipo, entrada, sl_orden, tp, lotes))
      g_ultima_entrada_operada = entrada_motor;
}

//+------------------------------------------------------------------+
//| CONSULTA AL MOTOR PROPIO (/api/setups)                            |
//+------------------------------------------------------------------+
bool ConsultarUltimoSetup(string &direccion, double &entrada, double &stop, double &tp, double &ajuste)
{
   string url = InpApiUrl + "?simbolo=" + InpSimboloConsulta + "&temporalidad=" + CodificarParametroUrl(InpTemporalidad)
              + "&cuenta=" + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)) + "&modo=" + ModoCuenta() + "&robot=" + MTB_ROBOT_ID;
   if(InpDiasHistorico > 0)
      url += "&dias=" + IntegerToString(InpDiasHistorico);
   string headers = "Authorization: Bearer " + MTB_LICENSE_TOKEN + "\r\n";
   char   datos[];
   char   respuesta[];
   string headers_respuesta;

   ResetLastError();
   // 15s, no 5s: /api/setups es una funcion serverless (Vercel) que importa pandas/numpy --
   // un "cold start" tras ~5-15min sin trafico (normal para un EA que solo llama 1 vez por
   // vela H1) puede tardar mas de 5s en responder. Confirmado en vivo 13 sep 2026: la MISMA
   // URL fallaba (WebRequest devolvia un status invalido, ~1003) en la primera llamada tras
   // inactividad y funcionaba normal (200) al reintentar de inmediato -- clasico cold start,
   // no un bug de la API ni de este EA.
   int status = WebRequest("GET", url, headers, 15000, datos, respuesta, headers_respuesta);

   if(status == -1)
   {
      int err = GetLastError();
      if(err == 4060)
         Print("ERROR WebRequest: URL no permitida. Agrega ", InpApiUrl, " en Herramientas > Opciones > Expert Advisors.");
      else
         Print("ERROR WebRequest: codigo ", err);
      return false;
   }
   if(status == 401 || status == 403)
   {
      EntrarStandby(CharArrayToString(respuesta));
      return false;
   }
   if(status != 200)
   {
      Print("API respondio HTTP ", status, ": ", CharArrayToString(respuesta));
      return false;
   }
   SalirStandby();

   string cuerpo = CharArrayToString(respuesta);
   if(!ExtraerUltimoSetupConfirmado(cuerpo, direccion, entrada, stop, tp)) return false;
   return CalcularAjuste(cuerpo, entrada, ajuste);
}

//+------------------------------------------------------------------+
//| Diferencia broker - motor en la MISMA vela 15m (ref_ts UTC). Sin  |
//| referencia -> ajuste 0 (API vieja). Falla cerrado si la           |
//| diferencia es absurda: casi seguro el grafico es otro simbolo.    |
//+------------------------------------------------------------------+
bool CalcularAjuste(const string &json, double entrada, double &ajuste)
{
   ajuste = 0;
   double ref_ts, ref_cierre;
   if(!ExtraerCampoNumeroDesde(json, "ref_ts", 0, ref_ts) || !ExtraerCampoNumeroDesde(json, "ref_cierre", 0, ref_cierre) || ref_cierre <= 0)
   {
      Print("Aviso: la API no mando precio de referencia -- se opera sin ajuste de precio");
      return true;
   }
   // hora del servidor del broker = UTC + su desfase (redondeado a 15 min)
   long desfase = (long)MathRound((double)(TimeTradeServer() - TimeGMT()) / 900.0) * 900;
   datetime t_broker = (datetime)((long)ref_ts + desfase);
   int barra = iBarShift(_Symbol, PERIOD_M15, t_broker, false);
   double cierre_broker = (barra >= 0) ? iClose(_Symbol, PERIOD_M15, barra) : 0;
   if(cierre_broker <= 0)
   {
      Print("No se pudo leer la vela 15m del broker para el ajuste de precio -- no se opera este setup");
      return false;
   }
   ajuste = cierre_broker - ref_cierre;
   double pct = MathAbs(ajuste) / ref_cierre * 100.0;
   if(pct > InpAjusteMaxPct)
   {
      Print("BLOQUEO: el precio del grafico (", _Symbol, " ", cierre_broker, ") difiere ", DoubleToString(pct, 2),
            "% del motor (", InpSimboloConsulta, " ", ref_cierre, "). Revisa que el grafico sea el simbolo correcto.");
      return false;
   }
   Print("Ajuste de precio ", _Symbol, ": ", DoubleToString(ajuste, _Digits), " (", DoubleToString(pct, 3), "%) -- motor ",
         ref_cierre, " vs broker ", cierre_broker, " en la vela ", TimeToString(t_broker));
   return true;
}

//+------------------------------------------------------------------+
//| LICENCIA -- modo de cuenta, standby y autorizacion por orden      |
//+------------------------------------------------------------------+
string ModoCuenta()
{
   return (AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_REAL) ? "real" : "demo";
}

void EntrarStandby(const string motivo)
{
   if(!g_standby)
      Print("STANDBY LOCK: operacion bloqueada por el Cerebro Master Trader -- ", motivo);
   g_standby = true;
   CancelarOrdenesPendientes(false); // una licencia rechazada no deja ordenes vivas
}

void SalirStandby()
{
   if(g_standby)
      Print("Licencia reautorizada por el Cerebro Master Trader -- se reanuda la operacion");
   g_standby = false;
}

//+------------------------------------------------------------------+
//| Pide autorizacion para UNA orden y los lotes calculados por el    |
//| servidor con la regla unica. Falla cerrado: cualquier respuesta   |
//| distinta de 200 con lotes > 0 cancela la orden.                   |
//+------------------------------------------------------------------+
bool AutorizarOrden(const string direccion, double entrada, double stop, double &lotes)
{
   lotes = 0;
   string payload = StringFormat(
      "{\"account\":%I64d,\"mode\":\"%s\",\"robot\":\"%s\",\"symbol\":\"%s\",\"type\":\"%s\","
      + "\"balance\":%.2f,\"riesgo_pct\":%.4f,\"entrada\":%.10f,\"stop\":%.10f,"
      + "\"tick_size\":%.10f,\"tick_value\":%.10f,\"vol_min\":%.4f,\"vol_max\":%.4f,\"vol_step\":%.4f}",
      AccountInfoInteger(ACCOUNT_LOGIN), ModoCuenta(), MTB_ROBOT_ID, _Symbol, direccion == "long" ? "buy" : "sell",
      AccountInfoDouble(ACCOUNT_BALANCE), EsSwingSM() ? InpRiskPercentSwing : InpRiskPercent, entrada, stop,
      SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE), SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE),
      SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN), SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX),
      SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP));

   char   datos[];
   char   respuesta[];
   string headers_respuesta;
   string headers = "Content-Type: application/json\r\nAuthorization: Bearer " + MTB_LICENSE_TOKEN + "\r\n";
   StringToCharArray(payload, datos, 0, StringLen(payload)); // sin el \0 final: el servidor espera JSON limpio

   ResetLastError();
   int status = WebRequest("POST", InpAuthUrl, headers, 15000, datos, respuesta, headers_respuesta);
   string cuerpo = CharArrayToString(respuesta);

   if(status == 401 || status == 403)
   {
      EntrarStandby(cuerpo);
      return false;
   }
   if(status != 200)
   {
      Print("BLOQUEO DE SEGURIDAD: autorizacion no disponible (HTTP ", status, ", err ", GetLastError(), ") -- orden cancelada");
      return false;
   }
   if(!ExtraerCampoNumeroDesde(cuerpo, "lotes", 0, lotes) || lotes <= 0)
   {
      Print("Orden no colocada: ", cuerpo); // p.ej. capital insuficiente para el lote minimo sin exceder el riesgo
      lotes = 0;
      return false;
   }
   Print("Autenticacion Master Trader OK: ", direccion, " ", _Symbol, " -- ", lotes, " lotes");
   return true;
}

//+------------------------------------------------------------------+
//| Codifica los unicos caracteres especiales que aparecen en los     |
//| valores de InpTemporalidad ("Swing (H)", etc.) -- no es un        |
//| url-encode general, MQL5 no trae uno y no hace falta aqui.        |
//+------------------------------------------------------------------+
string CodificarParametroUrl(const string valor)
{
   string r = valor;
   StringReplace(r, " ", "%20");
   StringReplace(r, "(", "%28");
   StringReplace(r, ")", "%29");
   return r;
}

//+------------------------------------------------------------------+
//| PARSER MINIMO DE JSON -- a proposito, no una libreria completa    |
//| El formato de /api/setups con InpTemporalidad es fijo y conocido  |
//| (ver tradebotbuilder/api/setups.py): {..., "setups":[...],         |
//| "setups_confirmados":[{...,"direccion":"long","entrada":F,         |
//| "stop":F,"tp":F}, ...]} (setups_confirmados es la ULTIMA llave)  |
//| -- se busca el marcador de "setups_confirmados"   |
//| primero y SOLO se lee direccion/entrada/stop despues de ese punto, |
//| nunca del arreglo "setups" crudo -- si "setups_confirmados" viene  |
//| vacio ([]) no hay nada que leer despues del marcador y se regresa  |
//| false, que es lo correcto (no operar un setup sin confirmar).      |
//+------------------------------------------------------------------+
bool ExtraerCampoStringDesde(const string &json, const string campo, int desde, string &valor)
{
   string buscar = "\"" + campo + "\":\"";
   int pos = StringFind(json, buscar, desde);
   if(pos < 0) return false;
   pos += StringLen(buscar);
   int fin = StringFind(json, "\"", pos);
   if(fin < 0) return false;
   valor = StringSubstr(json, pos, fin - pos);
   return true;
}

bool ExtraerCampoNumeroDesde(const string &json, const string campo, int desde, double &valor)
{
   string buscar = "\"" + campo + "\":";
   int pos = StringFind(json, buscar, desde);
   if(pos < 0) return false;
   pos += StringLen(buscar);
   int fin = pos;
   int largo = StringLen(json);
   while(fin < largo)
   {
      ushort c = StringGetCharacter(json, fin);
      if((c >= '0' && c <= '9') || c == '.' || c == '-') fin++;
      else break;
   }
   if(fin == pos) return false;
   valor = StringToDouble(StringSubstr(json, pos, fin - pos));
   return true;
}

bool ExtraerUltimoSetupConfirmado(const string &json, string &direccion, double &entrada, double &stop, double &tp)
{
   int marcador = StringFind(json, "\"setups_confirmados\":[");
   if(marcador < 0) return false; // API vieja sin confirmacion -- no operar por seguridad

   int pos_ultimo = -1;
   int desde = marcador;
   while(true)
   {
      int p = StringFind(json, "\"direccion\":\"", desde);
      if(p < 0) break;
      pos_ultimo = p;
      desde = p + 1;
   }
   if(pos_ultimo < 0) return false; // setups_confirmados vacio -- nada que operar

   if(!ExtraerCampoStringDesde(json, "direccion", pos_ultimo, direccion)) return false;
   if(!ExtraerCampoNumeroDesde(json, "entrada", pos_ultimo, entrada)) return false;
   if(!ExtraerCampoNumeroDesde(json, "stop", pos_ultimo, stop)) return false;
   if(!ExtraerCampoNumeroDesde(json, "tp", pos_ultimo, tp)) tp = 0; // sin tp -> respaldo InpTakeProfitR en OnTick
   return true;
}

//+------------------------------------------------------------------+
//| ORDEN PENDIENTE (limite en el punto medio del FVG)                |
//+------------------------------------------------------------------+
bool ColocarOrdenPendiente(ENUM_ORDER_TYPE tipo, double precio, double sl, double tp, double lots)
{
   MqlTradeRequest request = {};
   MqlTradeResult  result  = {};

   request.action       = TRADE_ACTION_PENDING;
   request.symbol        = _Symbol;
   request.volume        = lots;
   request.type          = tipo;
   request.price         = NormalizeDouble(precio, _Digits);
   request.sl             = NormalizeDouble(sl, _Digits);
   request.tp             = NormalizeDouble(tp, _Digits);
   request.magic          = InpMagicNumber;
   request.comment        = InpComment;
   request.type_time      = ORDER_TIME_SPECIFIED;
   request.expiration     = TimeCurrent() + InpVelasExpiracion * PeriodSeconds(InpTF);

   if(!OrderSend(request, result))
   {
      Print("ERROR al colocar orden pendiente: ", GetLastError());
      return false;
   }
   Print("Orden pendiente colocada: ", EnumToString(tipo), " | Precio: ", precio, " | SL: ", sl, " | TP: ", tp, " | Lotes: ", lots);
   return true;
}

//+------------------------------------------------------------------+
//| CANCELAR ORDENES PENDIENTES YA VENCIDAS DE ESTE EA                |
//| (por si el broker no expira automaticamente la orden)             |
//+------------------------------------------------------------------+
void CancelarOrdenesVencidas()
{
   CancelarOrdenesPendientes(true);
}

//+------------------------------------------------------------------+
//| CANCELAR ORDENES PENDIENTES CUYO TP YA SE TOCO SIN LLENAR         |
//| Long: Bid >= TP; short: Ask <= TP (misma regla que el backtest).  |
//+------------------------------------------------------------------+
void CancelarOrdenesPorTP()
{
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(!OrderSelect(ticket)) continue;
      if(OrderGetInteger(ORDER_MAGIC) != InpMagicNumber) continue;
      if(OrderGetString(ORDER_SYMBOL) != _Symbol) continue;
      double tp_orden = OrderGetDouble(ORDER_TP);
      if(tp_orden <= 0) continue;
      ENUM_ORDER_TYPE tipo = (ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE);
      bool toco_tp = (tipo == ORDER_TYPE_BUY_LIMIT  && SymbolInfoDouble(_Symbol, SYMBOL_BID) >= tp_orden)
                  || (tipo == ORDER_TYPE_SELL_LIMIT && SymbolInfoDouble(_Symbol, SYMBOL_ASK) <= tp_orden);
      if(!toco_tp) continue;
      MqlTradeRequest request = {};
      MqlTradeResult  result  = {};
      request.action = TRADE_ACTION_REMOVE;
      request.order   = ticket;
      if(OrderSend(request, result))
         Print("Orden pendiente #", ticket, " cancelada: el precio llego al TP (", tp_orden, ") antes de la entrada");
      else
         Print("ERROR al cancelar orden pendiente #", ticket, " por TP: ", GetLastError(), " (retcode ", result.retcode, ")");
   }
}

void CancelarOrdenesPendientes(bool solo_vencidas)
{
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(!OrderSelect(ticket)) continue;
      if(OrderGetInteger(ORDER_MAGIC) != InpMagicNumber) continue;
      datetime expiracion = (datetime)OrderGetInteger(ORDER_TIME_EXPIRATION);
      if(!solo_vencidas || (expiracion > 0 && TimeCurrent() >= expiracion))
      {
         MqlTradeRequest request = {};
         MqlTradeResult  result  = {};
         request.action = TRADE_ACTION_REMOVE;
         request.order   = ticket;
         if(!OrderSend(request, result))
            Print("ERROR al cancelar orden pendiente #", ticket, ": ", GetLastError(), " (retcode ", result.retcode, ")");
      }
   }
}

//+------------------------------------------------------------------+
//| CONTEO DE POSICIONES / ORDENES DE ESTE EA                         |
//+------------------------------------------------------------------+
//+------------------------------------------------------------------+
//| Cierra a mercado las posiciones de este EA cuya perdida flotante  |
//| (profit + swap) llega a InpPerdidaMaxPct del capital.             |
//+------------------------------------------------------------------+
void CerrarPorPerdidaMaxima()
{
   // ponytail: capital = balance actual; con una posicion por EA es el balance al abrir,
   // salvo que otro EA de la misma cuenta cierre operaciones en medio.
   double limite = -AccountInfoDouble(ACCOUNT_BALANCE) * InpPerdidaMaxPct / 100.0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || !PositionSelectByTicket(ticket) || PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;
      double perdida = PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);
      if(perdida > limite) continue;

      bool larga = PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY;
      MqlTradeRequest request = {};
      MqlTradeResult  result  = {};
      request.action    = TRADE_ACTION_DEAL;
      request.position  = ticket;
      request.symbol    = PositionGetString(POSITION_SYMBOL);
      request.volume    = PositionGetDouble(POSITION_VOLUME);
      request.type      = larga ? ORDER_TYPE_SELL : ORDER_TYPE_BUY;
      request.price     = larga ? SymbolInfoDouble(request.symbol, SYMBOL_BID) : SymbolInfoDouble(request.symbol, SYMBOL_ASK);
      request.deviation = 50;
      request.magic     = InpMagicNumber;
      request.comment   = "MTB-perdida-max";
      request.type_filling = FillingDelSimbolo(request.symbol);
      if(OrderSend(request, result))
         Print("CIERRE FORZADO -", InpPerdidaMaxPct, "%: ticket ", ticket, " perdida ", perdida, " (limite ", limite, ")");
      else
         Print("ERROR cierre forzado ticket ", ticket, ": ", GetLastError(), " retcode ", result.retcode);
   }
}

//+------------------------------------------------------------------+
//| Breakeven escalonado: el SL sube (nunca baja) segun la ganancia   |
//| en R. Posiciones sin SL (Swing S/M) no se tocan aqui.            |
//+------------------------------------------------------------------+
void MoverBreakeven()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || !PositionSelectByTicket(ticket) || PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;

      string simbolo = PositionGetString(POSITION_SYMBOL);
      bool   larga   = PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY;
      double entrada = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl      = PositionGetDouble(POSITION_SL);
      double tp      = PositionGetDouble(POSITION_TP);

      // R = distancia del SL original; se guarda la primera vez que se ve la posicion
      string clave = "MTB_R_" + IntegerToString((long)ticket);
      double r = GlobalVariableCheck(clave) ? GlobalVariableGet(clave) : 0.0;
      if(r <= 0)
      {
         if(sl <= 0) continue;                          // sin SL (Swing S/M): no aplica
         r = MathAbs(entrada - sl);
         if(r <= 0) continue;
         GlobalVariableSet(clave, r);
      }

      double precio   = larga ? SymbolInfoDouble(simbolo, SYMBOL_BID) : SymbolInfoDouble(simbolo, SYMBOL_ASK);
      double ganancia = (larga ? precio - entrada : entrada - precio) / r;
      double escalon;                                    // en R, donde debe quedar el SL
      if(ganancia >= 2.0)      escalon = 1.0;
      else if(ganancia >= 1.5) escalon = 0.5;
      else if(ganancia >= 1.0) escalon = 0.0;           // ponytail: breakeven = entrada; XM standard no cobra comision aparte
      else continue;

      int    digitos = (int)SymbolInfoInteger(simbolo, SYMBOL_DIGITS);
      double punto   = SymbolInfoDouble(simbolo, SYMBOL_POINT);
      double minimo  = SymbolInfoInteger(simbolo, SYMBOL_TRADE_STOPS_LEVEL) * punto; // distancia minima del broker
      double nuevo   = NormalizeDouble(larga ? entrada + escalon * r : entrada - escalon * r, digitos);
      string motivo  = "escalon +" + DoubleToString(escalon, 1) + "R";

      // desde 2R manda la estructura si protege mas que el escalon
      if(ganancia >= 2.0)
      {
         double est = NormalizeDouble(SLEstructura(simbolo, larga), digitos);
         if(est > 0 && MathAbs(precio - est) >= minimo && (larga ? (est > nuevo && est < precio) : (est < nuevo && est > precio)))
         {
            nuevo  = est;
            motivo = "estructura";
         }
      }
      if(sl > 0 && (larga ? nuevo <= sl + punto : nuevo >= sl - punto)) continue; // solo sube

      // si no cabe por la distancia minima del broker, se reintenta en el siguiente tick
      if(MathAbs(precio - nuevo) < minimo) continue;

      MqlTradeRequest request = {};
      MqlTradeResult  result  = {};
      request.action   = TRADE_ACTION_SLTP;
      request.position = ticket;
      request.symbol   = simbolo;
      request.sl       = nuevo;
      request.tp       = tp;
      request.magic    = InpMagicNumber;
      if(OrderSend(request, result))
         Print("SL ", DoubleToString(ganancia, 2), "R: ticket ", ticket, " SL -> ", nuevo, " (", motivo, ")");
      else
         Print("ERROR moviendo SL ticket ", ticket, ": ", GetLastError(), " retcode ", result.retcode);
   }
}

//+------------------------------------------------------------------+
//| Ultimo swing confirmado en InpTF (fractal: extremo mas bajo/alto  |
//| que 2 velas cerradas a cada lado), menos/mas el spread. 0 = nada. |
//+------------------------------------------------------------------+
double SLEstructura(const string simbolo, bool larga)
{
   const int k = 2; // ponytail: fractal de 2 velas; subir k si el trailing resulta demasiado nervioso
   double spread = SymbolInfoInteger(simbolo, SYMBOL_SPREAD) * SymbolInfoDouble(simbolo, SYMBOL_POINT);
   for(int i = k + 1; i < 200; i++)
   {
      double v = larga ? iLow(simbolo, InpTF, i) : iHigh(simbolo, InpTF, i);
      if(v <= 0) return 0;
      bool swing = true;
      for(int j = 1; j <= k && swing; j++)
      {
         double a = larga ? iLow(simbolo, InpTF, i - j) : iHigh(simbolo, InpTF, i - j);
         double b = larga ? iLow(simbolo, InpTF, i + j) : iHigh(simbolo, InpTF, i + j);
         swing = larga ? (v < a && v < b) : (v > a && v > b);
      }
      if(swing) return larga ? v - spread : v + spread;
   }
   return 0;
}

ENUM_ORDER_TYPE_FILLING FillingDelSimbolo(const string simbolo)
{
   long modos = SymbolInfoInteger(simbolo, SYMBOL_FILLING_MODE);
   if((modos & SYMBOL_FILLING_FOK) != 0) return ORDER_FILLING_FOK;
   if((modos & SYMBOL_FILLING_IOC) != 0) return ORDER_FILLING_IOC;
   return ORDER_FILLING_RETURN;
}

int CountPositions()
{
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0 && PositionSelectByTicket(ticket) && PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
         count++;
   }
   return count;
}

int CountOrdenesPendientes()
{
   int count = 0;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket > 0 && OrderSelect(ticket) && OrderGetInteger(ORDER_MAGIC) == InpMagicNumber)
         count++;
   }
   return count;
}
//+------------------------------------------------------------------+
