# -*- coding: utf-8 -*-
"""
Криптобиржи для робота: бессрочные USDT-фьючерсы (контракты) Bybit и Binance, демо-счёт и реальный счёт.

Цены и свечи всегда берутся с основной (публичной) площадки биржи — на демо-счетах Bybit и Binance котировки те же.
Заявки, баланс и позиции — с выбранного счёта (демо или реальный) по ключу API (key + secret).

Время свечей — UTC без пояса: торговый день робота начинается в 00:00 UTC (03:00 МСК).
Объём свечи "v" — в «лотах»: лот = шаг количества монеты на бирже (например, 0.001 BTC). Так робот считает
размер позиции целым числом лотов, как в роботе Мосбиржи; в заявку уходит лоты × шаг.

Ключи API хранятся только в settings.json рядом с программой (на сервере — в папке программы), в репозиторий не попадают.
"""
import hashlib
import hmac
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from urllib.parse import urlencode

import requests

UTC = timezone.utc

NAMES = {"bybit": "Bybit", "binance": "Binance"}
MODES = {"demo": "демо-счёт", "real": "реальный счёт"}

HOSTS = {
    ("bybit", "public"): ["https://api.bybit.com", "https://api.bytick.com"],
    ("bybit", "real"): ["https://api.bybit.com", "https://api.bytick.com"],
    ("bybit", "demo"): ["https://api-demo.bybit.com"],
    ("binance", "public"): ["https://fapi.binance.com"],
    ("binance", "real"): ["https://fapi.binance.com"],
    ("binance", "demo"): ["https://demo-fapi.binance.com", "https://testnet.binancefuture.com"],
}

TF_BYBIT = {1: "1", 5: "5", 15: "15", 60: "60", 1440: "D"}
TF_BINANCE = {1: "1m", 5: "5m", 15: "15m", 60: "1h", 1440: "1d"}

_session = threading.local()


def http():
    s = getattr(_session, "s", None)
    if s is None:
        s = _session.s = requests.Session()
        s.headers["User-Agent"] = "crypto-robot/1.0"
    return s


class ExchangeError(RuntimeError):
    pass


def _f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _ms(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def _naive_utc(ms):
    return datetime.fromtimestamp(int(ms) / 1000, UTC).replace(tzinfo=None)


def fmt_qty(qty, step):
    """Количество по шагу биржи строкой без экспоненты (0.001, 12, 0.5)."""
    d = (Decimal(str(qty)) / Decimal(str(step))).to_integral_value(ROUND_DOWN) * Decimal(str(step))
    s = format(d.normalize(), "f")
    return s


class Exchange:
    """Один класс на обе биржи. ex = "bybit" | "binance", mode = "demo" | "real"."""

    def __init__(self, ex="bybit", mode="demo", key="", secret=""):
        if ex not in NAMES:
            raise ValueError(f"неизвестная биржа {ex}")
        self.ex, self.mode = ex, ("real" if mode == "real" else "demo")
        self.key, self.secret = (key or "").strip(), (secret or "").strip()
        self.pub_hosts = list(HOSTS[(ex, "public")])
        self.priv_hosts = list(HOSTS[(ex, self.mode)])
        self._ins = {}            # symbol -> условия контракта
        self._ins_t = 0
        self._tick = {}           # symbol -> тикер за 24 часа
        self._tick_t = 0
        self._dt = 0              # поправка часов для подписи, мс
        self._lev = {}            # symbol -> установленное плечо
        self._lock = threading.Lock()

    @property
    def title(self):
        return f"{NAMES[self.ex]} — {MODES[self.mode]}"

    # ------------------------------------------------------------------ HTTP
    def _get_pub(self, path, params=None):
        last = None
        for i, base in enumerate(self.pub_hosts):
            for attempt in range(3):
                try:
                    r = http().get(base + path, params=params or {}, timeout=20)
                except requests.RequestException as e:
                    last = ExchangeError(f"нет связи с {NAMES[self.ex]} ({base}): {e}")
                    break
                if r.status_code in (418, 429) or r.status_code >= 500:
                    last = ExchangeError(f"{NAMES[self.ex]}: HTTP {r.status_code}")
                    time.sleep(1 + attempt * 2)
                    continue
                if r.status_code in (403, 451):
                    last = ExchangeError(f"{NAMES[self.ex]} не отвечает с этого адреса (HTTP {r.status_code}: доступ из страны сервера закрыт)")
                    break
                js = self._check(r)
                if i:                             # запасной адрес сработал — пусть он будет первым
                    self.pub_hosts.insert(0, self.pub_hosts.pop(i))
                return js
        raise last or ExchangeError("нет связи с биржей")

    def _check(self, r):
        try:
            js = r.json()
        except ValueError:
            raise ExchangeError(f"{NAMES[self.ex]}: HTTP {r.status_code} {r.text[:200]}")
        if self.ex == "bybit":
            if js.get("retCode", 0) != 0:
                raise ExchangeError(f"Bybit: {js.get('retMsg')} (код {js.get('retCode')})")
            return js.get("result", {})
        if isinstance(js, dict) and "code" in js and js.get("code", 0) not in (0, 200) and "msg" in js:
            raise ExchangeError(f"Binance: {js.get('msg')} (код {js.get('code')})")
        if r.status_code >= 400:
            raise ExchangeError(f"Binance: HTTP {r.status_code} {r.text[:200]}")
        return js

    def _sync_time(self, base):
        try:
            t0 = time.time()
            if self.ex == "bybit":
                js = http().get(base + "/v5/market/time", timeout=10).json()
                srv = int(js.get("time") or int(js["result"]["timeSecond"]) * 1000)
            else:
                srv = int(http().get(base + "/fapi/v1/time", timeout=10).json()["serverTime"])
            self._dt = srv - int((t0 + time.time()) / 2 * 1000)
        except Exception:
            pass

    def _priv(self, method, path, params=None):
        if not (self.key and self.secret):
            raise ExchangeError(f"не введены ключи API {self.title} — «Настройки» → «Биржа и счёт»")
        params = {k: v for k, v in (params or {}).items() if v is not None}
        last = None
        for i, base in enumerate(self.priv_hosts):
            if not self._dt:
                self._sync_time(base)
            for attempt in range(2):
                ts = str(int(time.time() * 1000) + self._dt)
                try:
                    if self.ex == "bybit":
                        recv = "10000"
                        if method == "GET":
                            q = urlencode(sorted(params.items()))
                            sign = hmac.new(self.secret.encode(), (ts + self.key + recv + q).encode(), hashlib.sha256).hexdigest()
                            h = {"X-BAPI-API-KEY": self.key, "X-BAPI-SIGN": sign, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": recv}
                            r = http().get(base + path + ("?" + q if q else ""), headers=h, timeout=20)
                        else:
                            body = json.dumps(params, separators=(",", ":"))
                            sign = hmac.new(self.secret.encode(), (ts + self.key + recv + body).encode(), hashlib.sha256).hexdigest()
                            h = {"X-BAPI-API-KEY": self.key, "X-BAPI-SIGN": sign, "X-BAPI-TIMESTAMP": ts,
                                 "X-BAPI-RECV-WINDOW": recv, "Content-Type": "application/json"}
                            r = http().post(base + path, data=body, headers=h, timeout=20)
                    else:
                        p = dict(params, timestamp=ts, recvWindow=10000)
                        q = urlencode(p)
                        sig = hmac.new(self.secret.encode(), q.encode(), hashlib.sha256).hexdigest()
                        h = {"X-MBX-APIKEY": self.key}
                        r = http().request(method, base + path + "?" + q + "&signature=" + sig, headers=h, timeout=20)
                except requests.RequestException as e:
                    last = ExchangeError(f"нет связи с {self.title} ({base}): {e}")
                    break
                if r.status_code == 429 or r.status_code >= 500:
                    last = ExchangeError(f"{self.title}: HTTP {r.status_code}")
                    time.sleep(2)
                    continue
                try:
                    js = self._check(r)
                except ExchangeError as e:
                    txt = str(e)
                    if ("10002" in txt or "-1021" in txt) and attempt == 0:      # часы разошлись — пересчитать поправку
                        self._sync_time(base)
                        continue
                    if r.status_code == 404 and i + 1 < len(self.priv_hosts):
                        last = e
                        break
                    raise
                if i:
                    self.priv_hosts.insert(0, self.priv_hosts.pop(i))
                return js
        raise last or ExchangeError("нет связи с биржей")

    # ------------------------------------------------------------------ контракты и тикеры
    def instruments(self, force=False):
        """Все бессрочные USDT-контракты, которые сейчас торгуются: symbol -> условия."""
        if self._ins and not force and time.time() - self._ins_t < 6 * 3600:
            return self._ins
        out = {}
        if self.ex == "bybit":
            cursor = ""
            for _ in range(10):
                js = self._get_pub("/v5/market/instruments-info", {"category": "linear", "limit": 1000, "cursor": cursor})
                for it in js.get("list", []):
                    if it.get("quoteCoin") != "USDT" or it.get("status") != "Trading" or it.get("contractType") != "LinearPerpetual":
                        continue
                    lf, pf, lv = it.get("lotSizeFilter", {}), it.get("priceFilter", {}), it.get("leverageFilter", {})
                    out[it["symbol"]] = {"lot": _f(lf.get("qtyStep"), 1), "min_qty": _f(lf.get("minOrderQty")),
                                         "max_mkt": _f(lf.get("maxMktOrderQty")) or None,
                                         "min_notional": _f(lf.get("minNotionalValue"), 5), "tick": _f(pf.get("tickSize")),
                                         "max_lev": _f(lv.get("maxLeverage"), 10), "base": it.get("baseCoin", ""),
                                         "short": True, "kind": "future"}
                cursor = js.get("nextPageCursor") or ""
                if not cursor:
                    break
        else:
            js = self._get_pub("/fapi/v1/exchangeInfo")
            for it in js.get("symbols", []):
                if it.get("quoteAsset") != "USDT" or it.get("status") != "TRADING" or it.get("contractType") != "PERPETUAL":
                    continue
                flt = {f["filterType"]: f for f in it.get("filters", [])}
                lot, mkt = flt.get("LOT_SIZE", {}), flt.get("MARKET_LOT_SIZE", {})
                out[it["symbol"]] = {"lot": _f(mkt.get("stepSize") or lot.get("stepSize"), 1),
                                     "min_qty": max(_f(lot.get("minQty")), _f(mkt.get("minQty"))),
                                     "max_mkt": _f(mkt.get("maxQty")) or None,
                                     "min_notional": _f(flt.get("MIN_NOTIONAL", {}).get("notional"), 5),
                                     "tick": _f(flt.get("PRICE_FILTER", {}).get("tickSize")), "max_lev": 20,
                                     "base": it.get("baseAsset", ""), "short": True, "kind": "future"}
        if out:
            self._ins, self._ins_t = out, time.time()
        return self._ins

    def share(self, sym):
        ins = self.instruments()
        if sym not in ins:
            ins = self.instruments(force=True)
        if sym not in ins:
            raise ExchangeError(f"{sym}: такого USDT-контракта на {NAMES[self.ex]} нет")
        return ins[sym]

    def tickers(self, max_age=20):
        """symbol -> {"last", "turnover" (USDT за 24 ч), "pct24", "open24"} по всем USDT-контрактам."""
        if self._tick and time.time() - self._tick_t < max_age:
            return self._tick
        out = {}
        if self.ex == "bybit":
            js = self._get_pub("/v5/market/tickers", {"category": "linear"})
            for t in js.get("list", []):
                s = t.get("symbol", "")
                if not s.endswith("USDT"):
                    continue
                out[s] = {"last": _f(t.get("lastPrice")), "turnover": _f(t.get("turnover24h")),
                          "pct24": _f(t.get("price24hPcnt")) * 100, "open24": _f(t.get("prevPrice24h"))}
        else:
            js = self._get_pub("/fapi/v1/ticker/24hr")
            for t in js:
                s = t.get("symbol", "")
                if not s.endswith("USDT"):
                    continue
                out[s] = {"last": _f(t.get("lastPrice")), "turnover": _f(t.get("quoteVolume")),
                          "pct24": _f(t.get("priceChangePercent")), "open24": _f(t.get("openPrice"))}
        if out:
            self._tick, self._tick_t = out, time.time()
        return self._tick

    def top_symbols(self, n=None):
        """Монеты по обороту за сутки, от большего к меньшему (n=None — все)."""
        ins = self.instruments()
        tk = self.tickers(max_age=300)
        syms = sorted((s for s in ins if s in tk), key=lambda s: -tk[s]["turnover"])
        return syms[:n] if n else syms

    def last_prices(self, syms):
        tk = self.tickers(max_age=3)
        return {s: tk[s]["last"] for s in syms if s in tk and tk[s]["last"]}

    # ------------------------------------------------------------------ свечи
    def candles(self, sym, frm, to, tf=5):
        """Свечи tf минут за frm..to (datetime с поясом или UTC без пояса). t — начало свечи, UTC без пояса;
        v — объём в лотах (монеты / шаг количества); q — оборот в USDT."""
        step = self.share(sym)["lot"] or 1
        a, b = _ms(frm), _ms(to)
        span = tf * 60_000
        out = {}
        lim = 1000 if self.ex == "bybit" else 1500
        while a < b:
            end = min(b, a + span * lim - 1)
            if self.ex == "bybit":
                js = self._get_pub("/v5/market/kline", {"category": "linear", "symbol": sym, "interval": TF_BYBIT[tf],
                                                        "start": a, "end": end, "limit": lim})
                rows = [(int(x[0]), x[1], x[2], x[3], x[4], x[5], x[6]) for x in js.get("list", [])]
            else:
                js = self._get_pub("/fapi/v1/klines", {"symbol": sym, "interval": TF_BINANCE[tf], "startTime": a,
                                                       "endTime": end, "limit": lim})
                rows = [(int(x[0]), x[1], x[2], x[3], x[4], x[5], x[7]) for x in js]
            for t, o, h, l, c, v, q in rows:
                if a <= t <= end:
                    out[t] = {"t": _naive_utc(t), "o": _f(o), "h": _f(h), "l": _f(l), "c": _f(c),
                              "v": _f(v) / step, "q": _f(q)}
            a = end + 1
        return [out[k] for k in sorted(out)]

    # ------------------------------------------------------------------ счёт
    def equity(self):
        """Стоимость счёта в USDT с учётом открытых позиций (нереализованный результат)."""
        if self.ex == "bybit":
            js = self._priv("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"})
            acc = (js.get("list") or [{}])[0]
            for c in acc.get("coin", []):
                if c.get("coin") == "USDT":
                    return _f(c.get("equity"))
            return _f(acc.get("totalEquity"))
        js = self._priv("GET", "/fapi/v2/account")
        return _f(js.get("totalMarginBalance")) or _f(js.get("totalWalletBalance"))

    def balance_info(self):
        """Для экрана: стоимость, свободно, нереализованный результат (USDT)."""
        if self.ex == "bybit":
            js = self._priv("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"})
            acc = (js.get("list") or [{}])[0]
            return {"equity": _f(acc.get("totalEquity")), "free": _f(acc.get("totalAvailableBalance")),
                    "upnl": _f(acc.get("totalPerpUPL"))}
        js = self._priv("GET", "/fapi/v2/account")
        return {"equity": _f(js.get("totalMarginBalance")), "free": _f(js.get("availableBalance")),
                "upnl": _f(js.get("totalUnrealizedProfit"))}

    def positions(self):
        """symbol -> количество монет (минус — шорт) по открытым позициям на бирже."""
        out = {}
        if self.ex == "bybit":
            cursor = ""
            for _ in range(10):
                js = self._priv("GET", "/v5/position/list", {"category": "linear", "settleCoin": "USDT", "limit": 200, "cursor": cursor or None})
                for p in js.get("list", []):
                    q = _f(p.get("size"))
                    if q:
                        out[p["symbol"]] = q if p.get("side") == "Buy" else -q
                cursor = js.get("nextPageCursor")
                if not cursor:
                    break
        else:
            for p in self._priv("GET", "/fapi/v2/positionRisk"):
                q = _f(p.get("positionAmt"))
                if q:
                    out[p["symbol"]] = q
        return out

    def max_leverage(self, sym):
        """Максимальное плечо монеты на бирже (маржинальные условия). Bybit — из списка контрактов (открытые данные);
        Binance — из таблицы плеч счёта (/fapi/v1/leverageBracket, нужен ключ), без ключа — ×20."""
        if self.ex == "bybit":
            return float(self.share(sym).get("max_lev") or 10)
        br = getattr(self, "_brackets", None)
        if (br is None or time.time() - getattr(self, "_brackets_t", 0) > 6 * 3600) and self.key and self.secret:
            try:
                js = self._priv("GET", "/fapi/v1/leverageBracket", {})
                br = {x["symbol"]: max(b_.get("initialLeverage", 1) for b_ in x.get("brackets", [{}]))
                      for x in (js if isinstance(js, list) else [js])}
                self._brackets, self._brackets_t = br, time.time()
            except Exception:
                br = br or {}
        return float((br or {}).get(sym) or 20)

    def set_leverage(self, sym, lev):
        lev = max(1, min(int(-(-float(lev) // 1)), int(self.max_leverage(sym))))
        if self._lev.get(sym) == lev:
            return lev
        try:
            if self.ex == "bybit":
                self._priv("POST", "/v5/position/set-leverage", {"category": "linear", "symbol": sym,
                                                                 "buyLeverage": str(lev), "sellLeverage": str(lev)})
            else:
                self._priv("POST", "/fapi/v1/leverage", {"symbol": sym, "leverage": lev})
        except ExchangeError as e:
            if "110043" not in str(e):        # Bybit: плечо уже такое
                raise
        self._lev[sym] = lev
        return lev

    def order(self, sym, qty, side, reduce=False):
        """Рыночная заявка: side > 0 — купить, < 0 — продать. reduce=True — только уменьшение позиции (закрытие).
        Возвращает среднюю цену исполнения (или None)."""
        ins = self.share(sym)
        qs = fmt_qty(qty, ins["lot"])
        if float(qs) <= 0:
            raise ExchangeError(f"{sym}: количество {qty} меньше шага {ins['lot']}")
        if self.ex == "bybit":
            js = self._priv("POST", "/v5/order/create", {"category": "linear", "symbol": sym,
                                                         "side": "Buy" if side > 0 else "Sell", "orderType": "Market",
                                                         "qty": qs, "reduceOnly": bool(reduce), "positionIdx": 0})
            oid = js.get("orderId")
            for _ in range(8):
                time.sleep(0.4)
                try:
                    r = self._priv("GET", "/v5/order/realtime", {"category": "linear", "symbol": sym, "orderId": oid})
                    lst = r.get("list") or []
                    if not lst:
                        r = self._priv("GET", "/v5/order/history", {"category": "linear", "symbol": sym, "orderId": oid})
                        lst = r.get("list") or []
                    if lst:
                        o = lst[0]
                        if o.get("orderStatus") in ("Rejected", "Cancelled") and not _f(o.get("cumExecQty")):
                            raise ExchangeError(f"Bybit отклонил заявку: {o.get('rejectReason') or o.get('orderStatus')}")
                        px = _f(o.get("avgPrice"))
                        if px:
                            return px
                except ExchangeError as e:
                    if "отклонил" in str(e):
                        raise
            return None
        p = {"symbol": sym, "side": "BUY" if side > 0 else "SELL", "type": "MARKET", "quantity": qs,
             "newOrderRespType": "RESULT"}
        if reduce:
            p["reduceOnly"] = "true"
        js = self._priv("POST", "/fapi/v1/order", p)
        if js.get("status") in ("REJECTED", "EXPIRED") and not _f(js.get("executedQty")):
            raise ExchangeError(f"Binance отклонил заявку: {js.get('status')}")
        return _f(js.get("avgPrice")) or None

    # ------------------------------------------------------------------ проверка связи
    def check(self):
        """Проверка: публичные данные и (если есть ключи) счёт. Возвращает список строк для журнала."""
        out = []
        t0 = time.time()
        try:
            n = len(self.instruments(force=True))
            tk = self.tickers(max_age=0)
            btc = tk.get("BTCUSDT", {}).get("last")
            btc_s = f"{btc:,.1f}".replace(",", " ") if btc else "—"
            out.append(f"✔ {NAMES[self.ex]}: данные отвечают ({self.pub_hosts[0]}, {int((time.time() - t0) * 1000)} мс) — "
                       f"{n} USDT-контрактов, BTC {btc_s} USDT")
        except Exception as e:
            out.append(f"✘ {NAMES[self.ex]}: данные не получены — {e}")
        if self.key and self.secret:
            try:
                b = self.balance_info()
                eq, fr = (f"{b['equity']:,.2f}".replace(",", " "), f"{b['free']:,.2f}".replace(",", " "))
                out.append(f"✔ {self.title}: ключ подходит, на счёте {eq} USDT, свободно {fr}")
            except Exception as e:
                out.append(f"✘ {self.title}: ключ не подошёл или нет прав на торговлю фьючерсами — {e}")
        else:
            out.append(f"• {self.title}: ключи API не введены — торговля пока невозможна")
        return out


def check_all(settings=None):
    """Проверка связи с обеими биржами (публичные данные) и ключами из настроек."""
    s = settings or {}
    lines = []
    for ex in ("bybit", "binance"):
        mode = s.get("mode", "demo") if s.get("exchange", "bybit") == ex else "demo"
        key, sec = (s.get(f"{ex}_{mode}_key", ""), s.get(f"{ex}_{mode}_secret", ""))
        lines += Exchange(ex, mode, key, sec).check()
    return lines
