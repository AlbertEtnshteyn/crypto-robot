"""Подбор стопа, безубытка и трейлинга «Движения дня» на истории: подбор на первых месяцах, проверка — на последних.
Запуск: python tools/optimize.py [дней] [месяцев на проверку]. Нужен доступ к data.binance.vision."""
import os, sys, json, itertools
from datetime import datetime, timedelta
from multiprocessing import Pool
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import robotcore as rc, scanner as sc

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 365
TEST_M = int(sys.argv[2]) if len(sys.argv) > 2 else 4
SHARE = 0.10                                         # доля счёта на сделку для оценки дохода счёта
s = dict(rc.DEFAULTS); s.update(exchange="binance", mid_modes=["both"])
sc.configure_exchange(s)
ti = sc.TInvest()
now = datetime.utcnow()
since = (now - timedelta(days=DAYS + 12)).date()
start = (now - timedelta(days=DAYS)).date()
split = (now - timedelta(days=int(TEST_M * 30.5))).date()
DATA = {}
for tk in sc.FALLBACK_TICKERS:
    try:
        rows = rc.bt_bars(ti, None, tk, since, lambda *a: None)
        if rows:
            DATA[tk] = rows
    except Exception as e:
        print(tk, "нет истории", e, flush=True)
print(f"монет {len(DATA)}, период {start} — {now:%Y-%m-%d}, подбор до {split}, проверка после", flush=True)

GRID = list(itertools.product((15, 60), ("swing", "trend", "24h", "1h"), (0.01, 0.02, 0.03), (0.02, 0.04, 0.06, 0.08, 0.12)))


def run(v):
    tf, sm, act, trl = v
    ss = dict(s); ss["mid_tfs"] = [tf]
    m = rc.mid_cfg(ss)
    m.update(cost=2 * 0.0005 + rc.BT_SLIP, fund={}, stop_mode=sm, act=act, trail=trl, trail_mode=True, no_time_exit=True)
    tr = []
    for tk, rows in DATA.items():
        tr += [t for t in rc.bt_run({tk: rows}, m, None) if t["date"] >= str(start)]
    tr.sort(key=lambda t: (t["date"], t["time"]))
    return v, [(t["date"], t["pct"]) for t in tr]


def stats(tr):
    """Сделки → сумма % на позицию, доход счёта (каждая сделка на SHARE счёта, сложный процент), просадка счёта, месяцы."""
    eq, peak, mdd, months = 1.0, 1.0, 0.0, {}
    for d, p in tr:
        eq *= 1 + SHARE * p / 100
        peak = max(peak, eq); mdd = min(mdd, eq / peak - 1)
        months[d[:7]] = months.get(d[:7], 0) + p
    return {"n": len(tr), "sum": sum(p for _, p in tr), "acc": (eq - 1) * 100, "dd": mdd * 100,
            "mplus": sum(1 for x in months.values() if x > 0), "m": len(months), "months": months}


name = lambda v: f"ТФ {v[0]} мин, стоп {({'swing': 'под откатом', 'trend': 'с начала тренда', '24h': 'лой 24 ч', '1h': 'лой часа'})[v[1]]}, б/у при +{v[2] * 100:g}%, трейлинг {v[3] * 100:g}%"
with Pool(os.cpu_count() or 2) as pool:
    res = dict(pool.map(run, GRID))
rows = []
for v, tr in res.items():
    a = stats([x for x in tr if x[0] < str(split)]); b = stats([x for x in tr if x[0] >= str(split)]); f = stats(tr)
    rows.append((v, a, b, f))
fmt = lambda x: f"сделок {x['n']}, счёт {x['acc']:+.1f}%, просадка {x['dd']:.1f}%, мес. в плюсе {x['mplus']}/{x['m']}"
print("\nТОП-15 ПО ПОДБОРУ (первые месяцы) → как они же прошли проверку (последние месяцы, не участвовали в подборе):", flush=True)
for v, a, b, f in sorted(rows, key=lambda r: -r[1]["acc"])[:15]:
    print(f"ПОДБОР {name(v)} | подбор: {fmt(a)} | ПРОВЕРКА: {fmt(b)} | за год: счёт {f['acc']:+.1f}%", flush=True)
print("\nТОП-10 ЗА ВЕСЬ ГОД (подогнано под историю — для справки):", flush=True)
for v, a, b, f in sorted(rows, key=lambda r: -r[3]["acc"])[:10]:
    print(f"ГОД {name(v)} | {fmt(f)} | по месяцам: {', '.join(f'{k[2:]} {x:+.0f}' for k, x in sorted(f['months'].items()))}", flush=True)
ok = [r for r in rows if r[1]["acc"] > 0 and r[2]["acc"] > 0]
print(f"\nВариантов в плюсе и на подборе, и на проверке: {len(ok)} из {len(rows)}", flush=True)
for v, a, b, f in sorted(ok, key=lambda r: -min(r[1]["acc"], r[2]["acc"]))[:10]:
    print(f"УСТОЙЧИВЫЙ {name(v)} | подбор {a['acc']:+.1f}%, проверка {b['acc']:+.1f}%, год {f['acc']:+.1f}%, просадка {f['dd']:.1f}%", flush=True)
os.makedirs("out", exist_ok=True)
json.dump([{"v": list(v), "fit": {k: a[k] for k in ("n", "acc", "dd")}, "test": {k: b[k] for k in ("n", "acc", "dd")},
            "year": {k: f[k] for k in ("n", "acc", "dd", "sum")}} for v, a, b, f in rows], open("out/optimize.json", "w"), ensure_ascii=False)
