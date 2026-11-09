"""Стратегия «всплеск объёма — быстрый вход»: свеча с оборотом ≥ K × медианы за сутки → вход по её цвету на открытии
следующей свечи, тейк +2%, стоп (за свечой / 1% / 2%). Подбор на первых месяцах, проверка на последних.
Запуск: python tools/spike_bt.py [дней] [месяцев на проверку]."""
import os, sys, itertools
from datetime import datetime, timedelta
from multiprocessing import Pool
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import robotcore as rc, scanner as sc

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 365
TEST_M = int(sys.argv[2]) if len(sys.argv) > 2 else 4
TAKE, COST, SHARE = 0.02, 2 * 0.0005 + 0.0004, 0.10      # тейк 2%, комиссия тейкер ×2 + проскальзывание, доля счёта на сделку
s = dict(rc.DEFAULTS); s.update(exchange="binance")
sc.configure_exchange(s)
ti = sc.TInvest()
now = datetime.utcnow()
since = (now - timedelta(days=DAYS + 12)).date()
start, split = str((now - timedelta(days=DAYS)).date()), str((now - timedelta(days=int(TEST_M * 30.5))).date())
DATA = {}
for tk in sc.FALLBACK_TICKERS:
    try:
        rows = rc.bt_bars(ti, None, tk, since, lambda *a: None)
        if rows:
            DATA[tk] = rows
    except Exception as e:
        print(tk, "нет истории", e, flush=True)
print(f"монет {len(DATA)}, период {start} — {now:%Y-%m-%d}, подбор до {split}, проверка после", flush=True)


def bars(rows, tf):
    out = []
    for t, o, h, l, c, *r in rows:
        v = r[0] if r else 0
        k = t.replace(minute=t.minute - t.minute % tf, second=0)
        if out and out[-1][0] == k:
            b = out[-1]; b[2] = max(b[2], h); b[3] = min(b[3], l); b[4] = c; b[5] += v
        else:
            out.append([k, o, h, l, c, v])
    return out


def ema(v, n):
    a, e, o = 2 / (n + 1), None, []
    for x in v:
        e = x if e is None else e + a * (x - e); o.append(e)
    return o


def run(var):
    tf, K, stop_m, trend = var
    out = []
    per_day = 1440 // tf
    for tk, rows in DATA.items():
        B = bars(rows, tf)
        cl = [b[4] for b in B]
        e200 = ema(cl, 200)
        vols = [b[5] for b in B]
        i, n = per_day, len(B)
        while i < n - 1:
            b = B[i]
            prev = sorted(vols[i - per_day:i]); med = prev[len(prev) // 2]
            if med <= 0 or b[5] < K * med or b[4] == b[1] or str(b[0].date()) < start:
                i += 1; continue
            sd = 1 if b[4] > b[1] else -1
            if trend:
                if i < 200:
                    i += 1; continue
                s200 = sum(cl[i - 199:i + 1]) / 200
                if (b[4] - e200[i]) * sd <= 0 or (b[4] - s200) * sd <= 0:
                    i += 1; continue
            px = B[i + 1][1]                                   # вход на открытии следующей свечи
            if stop_m == "candle":
                stop = b[3] * 0.999 if sd > 0 else b[2] * 1.001
                if (px - stop) * sd <= 0:
                    i += 1; continue
            else:
                stop = px * (1 - sd * stop_m)
            take = px * (1 + sd * TAKE)
            res, j = None, i + 1
            while j < n:
                h, l = B[j][2], B[j][3]
                if (l <= stop) if sd > 0 else (h >= stop):     # стоп и тейк в одной свече — считаем стоп
                    res = sd * (stop / px - 1); break
                if (h >= take) if sd > 0 else (l <= take):
                    res = TAKE; break
                j += 1
            if res is None:
                res, j = sd * (B[-1][4] / px - 1), n - 1
            out.append((str(b[0].date()), str(b[0]), (res - COST) * 100, (B[j][0] - B[i + 1][0]).total_seconds() / 3600))
            i = j + 1                                          # одна позиция по монете за раз
    out.sort()
    return var, out


def st(tr):
    eq, peak, dd, months = 1.0, 1.0, 0.0, {}
    for d, _, p, _h in tr:
        eq *= 1 + SHARE * p / 100; peak = max(peak, eq); dd = min(dd, eq / peak - 1)
        months[d[:7]] = months.get(d[:7], 0) + p
    w = sum(1 for x in tr if x[2] > 0)
    hold = sorted(x[3] for x in tr)
    return {"n": len(tr), "win": w / max(1, len(tr)) * 100, "acc": (eq - 1) * 100, "dd": dd * 100,
            "avg": sum(x[2] for x in tr) / max(1, len(tr)), "mplus": sum(1 for v in months.values() if v > 0), "m": len(months),
            "hold": hold[len(hold) // 2] if hold else 0, "months": months}


GRID = list(itertools.product((5, 15), (3, 5, 10), ("candle", 0.01, 0.02), (False, True)))
nm = lambda v: (f"ТФ {v[0]} мин, всплеск ×{v[1]}, стоп {'за свечой всплеска' if v[2] == 'candle' else f'{v[2] * 100:g}%'}, "
                f"{'по тренду EMA200/SMA200' if v[3] else 'без фильтра тренда'}")
fmt = lambda x: f"сделок {x['n']}, в плюсе {x['win']:.0f}%, средняя {x['avg']:+.2f}%, счёт {x['acc']:+.1f}%, просадка {x['dd']:.0f}%"
with Pool(os.cpu_count() or 2) as pool:
    res = dict(pool.map(run, GRID))
rows = [(v, st([x for x in tr if x[0] < split]), st([x for x in tr if x[0] >= split]), st(tr)) for v, tr in res.items()]
print("\nВСЕ ВАРИАНТЫ ЗА ГОД (сортировка по доходу счёта, 10% счёта на сделку, без плеча):", flush=True)
for v, a, b, f in sorted(rows, key=lambda r: -r[3]["acc"]):
    print(f"ВАР {nm(v)} | год: {fmt(f)}, мес. в плюсе {f['mplus']}/{f['m']}, держим ~{f['hold']:.1f} ч | подбор {a['acc']:+.1f}% / ПРОВЕРКА {b['acc']:+.1f}%", flush=True)
best = max(rows, key=lambda r: r[1]["acc"])
print(f"\nЛУЧШИЙ НА ПОДБОРЕ: {nm(best[0])} | подбор {fmt(best[1])} | ПРОВЕРКА {fmt(best[2])}", flush=True)
print("ПО МЕСЯЦАМ: " + ", ".join(f"{k[2:]} {x:+.0f}" for k, x in sorted(best[3]["months"].items())), flush=True)
print(f"УСТОЙЧИВЫХ (плюс и на подборе, и на проверке): {sum(1 for r in rows if r[1]['acc'] > 0 and r[2]['acc'] > 0)} из {len(rows)}", flush=True)
