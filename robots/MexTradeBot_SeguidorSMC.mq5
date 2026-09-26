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
//| cruza cada setup crudo contra la tendencia del dia y el backtest  |
//| de esa direccion antes de confirmarlo (ver api/setups.py). Si la  |
//| API responde sin ese campo (version vieja), este EA no opera --   |
//| mas seguro que asumir el setup crudo sin confirmar.                |
//|                                                                    |
//| SL/TP fijos (sin break-even ni trailing) A PROPOSITO: el TP a 2R  |
//| es exactamente lo que se valido en backtesting/backtest.py -- si  |
//| se agrega gestion dinamica aqui, los resultados en vivo dejan de  |
//| ser comparables al backtest.                                      |
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
#property version   "1.10"
#property strict

#define MTB_ROBOT_ID "seguidor-smc"   // id del robot en la licencia -- fijo, no editable por el cliente

//--- LICENCIA MASTER TRADER
input string MTB_LICENSE_TOKEN   = "";            // Token entregado por MexTradeBot (MTB-XXXXX-XXXXX-XXXXX-XXXXX)
input string InpAuthUrl          = "https://mextradebot-app.vercel.app/api/v1/auth"; // Autorizacion + lotes

//--- CONEXION AL MOTOR PROPIO
input string InpApiUrl           = "https://mextradebot-app.vercel.app/api/setups"; // URL de /api/setups
input string InpSimboloConsulta  = "XAUUSD";      // Simbolo tal como lo espera la API (ver conectividad.SIMBOLOS)
input string InpTemporalidad     = "Intraday";    // Scalping / Intraday / Swing (H) / Swing (S) / Swing (M)
input int    InpDiasHistorico    = 0;             // 0 = usa el default calibrado de la API para InpTemporalidad

//--- PARAMETROS DE OPERACION
input ENUM_TIMEFRAMES InpTF      = PERIOD_H1;     // Timeframe del disparo (nueva vela = nueva consulta)
input double InpRiskPercent      = 1.0;           // Riesgo por operacion (%) -- los lotes los calcula el servidor
input double InpTakeProfitR      = 2.0;           // Take profit en multiplos de R (igual que el backtest)
input int    InpVelasExpiracion  = 20;            // Velas que la orden pendiente espera antes de cancelarse
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
   Print("MexTradeBot_SeguidorSMC inicializado -- consultando ", InpApiUrl, " para ", InpSimboloConsulta, " (", InpTemporalidad, "), solo opera setups confirmados");
   Print("Licencia: cuenta ", AccountInfoInteger(ACCOUNT_LOGIN), " (", ModoCuenta(), "), robot ", MTB_ROBOT_ID);
   Print("IMPORTANTE: agrega 'https://mextradebot-app.vercel.app' (cubre ", InpApiUrl, " y ", InpAuthUrl, ") en Herramientas > Opciones > Expert Advisors > 'Permitir WebRequest para las URL siguientes', si no las consultas fallan.");
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| TICK PRINCIPAL                                                    |
//+------------------------------------------------------------------+
void OnTick()
{
   // Solo procesar en vela nueva -- evita golpear la API en cada tick
   datetime vela_actual = iTime(_Symbol, InpTF, 0);
   if(vela_actual == g_ultima_vela) return;
   g_ultima_vela = vela_actual;

   CancelarOrdenesVencidas();

   if(CountPositions() > 0 || CountOrdenesPendientes() > 0) return; // ya hay algo abierto/pendiente de este EA

   string direccion;
   double entrada, stop;
   if(!ConsultarUltimoSetup(direccion, entrada, stop)) return;
   if(g_standby) return;

   // evita re-operar exactamente el mismo setup si ya se coloco antes
   if(MathAbs(entrada - g_ultima_entrada_operada) < _Point) return;

   double riesgo = MathAbs(entrada - stop);
   if(riesgo <= 0) return; // geometria invalida, no deberia pasar (ya filtrado por detectar_setups)

   double tp = (direccion == "long") ? entrada + InpTakeProfitR * riesgo : entrada - InpTakeProfitR * riesgo;
   ENUM_ORDER_TYPE tipo = (direccion == "long") ? ORDER_TYPE_BUY_LIMIT : ORDER_TYPE_SELL_LIMIT;

   double lotes;
   if(!AutorizarOrden(direccion, entrada, stop, lotes)) return;

   if(ColocarOrdenPendiente(tipo, entrada, stop, tp, lotes))
      g_ultima_entrada_operada = entrada;
}

//+------------------------------------------------------------------+
//| CONSULTA AL MOTOR PROPIO (/api/setups)                            |
//+------------------------------------------------------------------+
bool ConsultarUltimoSetup(string &direccion, double &entrada, double &stop)
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
   return ExtraerUltimoSetupConfirmado(cuerpo, direccion, entrada, stop);
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
      AccountInfoDouble(ACCOUNT_BALANCE), InpRiskPercent, entrada, stop,
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
//| "stop":F}, ...]} -- se busca el marcador de "setups_confirmados"   |
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

bool ExtraerUltimoSetupConfirmado(const string &json, string &direccion, double &entrada, double &stop)
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
         OrderSend(request, result);
      }
   }
}

//+------------------------------------------------------------------+
//| CONTEO DE POSICIONES / ORDENES DE ESTE EA                         |
//+------------------------------------------------------------------+
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
