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
//| SL/TP fijos (sin break-even ni trailing) A PROPOSITO: el TP a 2R  |
//| es exactamente lo que se valido en backtesting/backtest.py -- si  |
//| se agrega gestion dinamica aqui, los resultados en vivo dejan de  |
//| ser comparables al backtest.                                      |
//+------------------------------------------------------------------+
#property copyright "MexTradeBot"
#property version   "1.00"
#property strict

//--- CONEXION AL MOTOR PROPIO
input string InpApiUrl           = "https://mextradebot-app.vercel.app/api/setups"; // URL de /api/setups
input string InpSimboloConsulta  = "XAUUSD";      // Simbolo tal como lo espera la API (ver conectividad.SIMBOLOS)
input int    InpDiasHistorico    = 30;            // Dias de historico a pedir en cada consulta

//--- PARAMETROS DE OPERACION
input ENUM_TIMEFRAMES InpTF      = PERIOD_H1;     // Timeframe del disparo (nueva vela = nueva consulta)
input double InpRiskPercent      = 1.0;           // Riesgo por operacion (%)
input double InpTakeProfitR      = 2.0;           // Take profit en multiplos de R (igual que el backtest)
input int    InpVelasExpiracion  = 20;            // Velas que la orden pendiente espera antes de cancelarse
input int    InpMagicNumber      = 20260828;      // Numero magico unico del EA
input string InpComment          = "MTB-SMC";     // Comentario en operaciones

//--- ESTADO INTERNO
datetime g_ultima_vela = 0;
double   g_ultima_entrada_operada = 0;

//+------------------------------------------------------------------+
//| INICIALIZACION                                                    |
//+------------------------------------------------------------------+
int OnInit()
{
   if(StringLen(InpApiUrl) == 0)
   {
      Print("ERROR: InpApiUrl vacio");
      return INIT_FAILED;
   }
   Print("MexTradeBot_SeguidorSMC inicializado -- consultando ", InpApiUrl, " para ", InpSimboloConsulta);
   Print("IMPORTANTE: agrega '", InpApiUrl, "' en Herramientas > Opciones > Expert Advisors > 'Permitir WebRequest para las URL siguientes', si no las consultas fallan.");
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

   // evita re-operar exactamente el mismo setup si ya se coloco antes
   if(MathAbs(entrada - g_ultima_entrada_operada) < _Point) return;

   double riesgo = MathAbs(entrada - stop);
   if(riesgo <= 0) return; // geometria invalida, no deberia pasar (ya filtrado por detectar_setups)

   double tp = (direccion == "long") ? entrada + InpTakeProfitR * riesgo : entrada - InpTakeProfitR * riesgo;
   ENUM_ORDER_TYPE tipo = (direccion == "long") ? ORDER_TYPE_BUY_LIMIT : ORDER_TYPE_SELL_LIMIT;

   if(ColocarOrdenPendiente(tipo, entrada, stop, tp))
      g_ultima_entrada_operada = entrada;
}

//+------------------------------------------------------------------+
//| CONSULTA AL MOTOR PROPIO (/api/setups)                            |
//+------------------------------------------------------------------+
bool ConsultarUltimoSetup(string &direccion, double &entrada, double &stop)
{
   string url = InpApiUrl + "?simbolo=" + InpSimboloConsulta + "&dias=" + IntegerToString(InpDiasHistorico);
   string headers = "";
   char   datos[];
   char   respuesta[];
   string headers_respuesta;

   ResetLastError();
   int status = WebRequest("GET", url, headers, 5000, datos, respuesta, headers_respuesta);

   if(status == -1)
   {
      int err = GetLastError();
      if(err == 4060)
         Print("ERROR WebRequest: URL no permitida. Agrega ", InpApiUrl, " en Herramientas > Opciones > Expert Advisors.");
      else
         Print("ERROR WebRequest: codigo ", err);
      return false;
   }
   if(status != 200)
   {
      Print("API respondio HTTP ", status, ": ", CharArrayToString(respuesta));
      return false;
   }

   string cuerpo = CharArrayToString(respuesta);
   return ExtraerUltimoSetup(cuerpo, direccion, entrada, stop);
}

//+------------------------------------------------------------------+
//| PARSER MINIMO DE JSON -- a proposito, no una libreria completa    |
//| El formato de /api/setups es fijo y conocido (ver                 |
//| tradebotbuilder/api/setups.py y motor_smc/setup_ob_fvg.py):        |
//| {"simbolo":.., "velas":N, "setups":[{...,"direccion":"long",       |
//| "entrada":F, "stop":F}, ...]} -- se toma el ULTIMO objeto del      |
//| arreglo (el setup mas reciente).                                   |
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

bool ExtraerUltimoSetup(const string &json, string &direccion, double &entrada, double &stop)
{
   int pos_ultimo = -1;
   int desde = 0;
   while(true)
   {
      int p = StringFind(json, "\"direccion\":\"", desde);
      if(p < 0) break;
      pos_ultimo = p;
      desde = p + 1;
   }
   if(pos_ultimo < 0) return false; // sin setups en la ventana consultada

   if(!ExtraerCampoStringDesde(json, "direccion", pos_ultimo, direccion)) return false;
   if(!ExtraerCampoNumeroDesde(json, "entrada", pos_ultimo, entrada)) return false;
   if(!ExtraerCampoNumeroDesde(json, "stop", pos_ultimo, stop)) return false;
   return true;
}

//+------------------------------------------------------------------+
//| ORDEN PENDIENTE (limite en el punto medio del FVG)                |
//+------------------------------------------------------------------+
bool ColocarOrdenPendiente(ENUM_ORDER_TYPE tipo, double precio, double sl, double tp)
{
   double lots = CalcularLotes(MathAbs(precio - sl));
   if(lots <= 0)
   {
      Print("ERROR: lote calculado invalido, no se coloca la orden");
      return false;
   }

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
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(!OrderSelect(ticket)) continue;
      if(OrderGetInteger(ORDER_MAGIC) != InpMagicNumber) continue;
      datetime expiracion = (datetime)OrderGetInteger(ORDER_TIME_EXPIRATION);
      if(expiracion > 0 && TimeCurrent() >= expiracion)
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
//| CALCULAR LOTES POR RIESGO (mismo patron que el resto de MexTradeBot) |
//+------------------------------------------------------------------+
double CalcularLotes(double riesgo_precio)
{
   double balance     = AccountInfoDouble(ACCOUNT_BALANCE);
   double riesgo_dinero = balance * InpRiskPercent / 100.0;
   double tick_value  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tick_size   = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tick_size <= 0) return 0;

   double valor_riesgo = riesgo_precio / tick_size * tick_value;
   if(valor_riesgo <= 0) return 0;

   double lots = riesgo_dinero / valor_riesgo;

   double min_lot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double max_lot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double lot_step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   lots = MathFloor(lots / lot_step) * lot_step;
   lots = MathMax(min_lot, MathMin(max_lot, lots));
   return lots;
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
