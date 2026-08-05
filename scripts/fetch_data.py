#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
JPY/TWD 購匯決策儀表板 — 資料抓取
輸出 data/latest.json（供 index.html 讀取）＋ data/history/YYYY-MM-DD.json 快照

資料源（全部免費）：
  台銀牌告        rate.bot.com.tw            免金鑰 · 需台灣 IP
  匯率中價        Yahoo / open.er-api        免金鑰 · 三源備援
  日本公債        財務省 jgbcm_all.csv        免金鑰
  美國公債        FRED DGS2/DGS10            需免費金鑰（環境變數 FRED_API_KEY）
  投機部位        CFTC Socrata               免金鑰
  日本資金流      財務省 week.csv             免金鑰
  日本核心CPI     e-Stat                     免金鑰
  日本貿易        財務省 customs             免金鑰
  介入實績        財務省 feio                免金鑰
"""
import os, re, io, csv, json, math, datetime as dt, warnings
from pathlib import Path
import requests, numpy as np, pandas as pd

warnings.filterwarnings('ignore')
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'data'; DATA.mkdir(exist_ok=True); (DATA/'history').mkdir(exist_ok=True)
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36'}
FRED_KEY = os.environ.get('FRED_API_KEY', 'fbe7b81e83ea7db772be4404903cb0ab')
NEED = int(os.environ.get('JPY_TARGET', '4000000'))
WARN = []          # 收集失效訊息，顯示在頁面上
def warn(m):
    WARN.append(m); print('  ! ' + m)

def get(url, **kw):
    kw.setdefault('headers', UA); kw.setdefault('timeout', 45)
    return requests.get(url, **kw)

# ─────────── 1. 匯率中價與歷史（三源備援） ───────────
def fetch_rates():
    """回傳 (df[JPYTWD,USDTWD,USDJPY] 日序列, 來源名)"""
    # 主源：Yahoo（歷史最長）
    try:
        import yfinance as yf
        d = yf.download(['JPYTWD=X', 'TWD=X', 'JPY=X'], start='2017-01-01',
                        progress=False, auto_adjust=False, threads=False)['Close'].dropna()
        d.columns = ['JPYTWD', 'USDJPY', 'USDTWD'] if list(d.columns) == ['JPYTWD=X','JPY=X','TWD=X'] else d.columns
        d = d.rename(columns={'JPYTWD=X':'JPYTWD','JPY=X':'USDJPY','TWD=X':'USDTWD'})
        if len(d) > 500:
            print(f'  rates: Yahoo {len(d)} 列 {d.index[0].date()}~{d.index[-1].date()}')
            return d, 'Yahoo Finance'
        warn('Yahoo 回傳列數過少，改用備援')
    except Exception as e:
        warn(f'Yahoo 抓取失敗（{str(e)[:60]}），改用備援')
    # 備援：frankfurter(USD/JPY 歷史) + open.er-api(當日 TWD)
    try:
        fj = get('https://api.frankfurter.app/2017-01-01..?from=USD&to=JPY').json()['rates']
        er = get('https://open.er-api.com/v6/latest/USD').json()['rates']
        s = pd.Series({pd.Timestamp(k): v['JPY'] for k, v in fj.items()}).sort_index()
        d = pd.DataFrame({'USDJPY': s})
        d['USDTWD'] = er['TWD']            # 僅當日值，歷史以最新值填（分位會失真，故標警示）
        d['JPYTWD'] = d['USDTWD'] / d['USDJPY']
        warn('使用備援匯率源：USD/TWD 無歷史，分位定位僅供參考')
        return d, 'frankfurter + open.er-api（備援）'
    except Exception as e:
        raise SystemExit(f'所有匯率來源皆失敗：{e}')

# ─────────── 2. 台銀牌告（需台灣 IP） ───────────
def fetch_bot():
    out = {'src': 'rate.bot.com.tw', 'ok': False,
           'jpy_cash_buy': None, 'jpy_cash_sell': None,
           'jpy_spot_buy': None, 'jpy_spot_sell': None,
           'usd_spot_buy': None, 'usd_spot_sell': None, 'quoted_at': None}
    try:
        r = get('https://rate.bot.com.tw/xrt/flcsv/0/day')
        txt = r.content.decode('utf-8-sig', errors='replace')
        if '<!DOCTYPE' in txt[:200] or len(r.content) < 500:
            warn('台銀牌告被擋（境外 IP？）— 沿用上次數值，請用台灣 runner')
            return out
        for row in csv.reader(io.StringIO(txt)):
            if not row or row[0] not in ('JPY', 'USD'):
                continue
            # 欄位：幣別,現金買入,-,即期買入,...,現金賣出,-,即期賣出,...
            try:
                cb, sb = float(row[2]), float(row[3])
                cs, ss = float(row[12]), float(row[13])
            except (IndexError, ValueError):
                continue
            p = 'jpy' if row[0] == 'JPY' else 'usd'
            out[f'{p}_cash_buy'], out[f'{p}_spot_buy'] = cb, sb
            out[f'{p}_cash_sell'], out[f'{p}_spot_sell'] = cs, ss
        out['ok'] = out['jpy_spot_sell'] is not None
        out['quoted_at'] = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
        if out['ok']:
            print(f"  台銀: JPY 即期賣出 {out['jpy_spot_sell']} 現金賣出 {out['jpy_cash_sell']}")
    except Exception as e:
        warn(f'台銀牌告抓取失敗：{str(e)[:60]}')
    return out

# ─────────── 3. 日本公債（財務省） ───────────
BASE_ERA = {'S': 1925, 'H': 1988, 'R': 2018, 'T': 1911}
def fetch_jgb():
    r = get('https://www.mof.go.jp/jgbs/reference/interest_rate/data/jgbcm_all.csv')
    txt = r.content.decode('shift_jis', errors='replace')
    rec = []
    for line in txt.splitlines()[2:]:
        p = line.split(',')
        m = re.match(r'^([SHRT])(\d+)\.(\d+)\.(\d+)$', p[0].strip())
        if not m: continue
        try:
            rec.append((pd.Timestamp(BASE_ERA[m[1]] + int(m[2]), int(m[3]), int(m[4])),
                        float(p[2]), float(p[10])))
        except Exception:
            pass
    df = pd.DataFrame(rec, columns=['d', 'y2', 'y10']).set_index('d').sort_index()
    print(f'  JGB: {len(df)} 列，最新 {df.index[-1].date()} 2Y={df.y2.iloc[-1]} 10Y={df.y10.iloc[-1]}')
    return df

# ─────────── 4. 美國公債（FRED） ───────────
def fetch_fred(sid, start='2017-01-01'):
    if not FRED_KEY:
        warn('FRED_API_KEY 未設定，美債利率不可得'); return pd.Series(dtype=float)
    j = get('https://api.stlouisfed.org/fred/series/observations',
            params={'series_id': sid, 'api_key': FRED_KEY, 'file_type': 'json',
                    'observation_start': start}).json()
    s = pd.Series({o['date']: (np.nan if o['value'] == '.' else float(o['value']))
                   for o in j['observations']})
    s.index = pd.to_datetime(s.index)
    return s.dropna()

# ─────────── 5. CFTC 投機部位 ───────────
def fetch_cftc():
    j = get('https://publicreporting.cftc.gov/resource/6dca-aqww.json',
            params={'$where': "cftc_contract_market_code='097741' AND report_date_as_yyyy_mm_dd>='2017-01-01'",
                    '$limit': 50000, '$order': 'report_date_as_yyyy_mm_dd'}).json()
    df = pd.DataFrame(j)
    df['d'] = pd.to_datetime(df['report_date_as_yyyy_mm_dd'])
    for c in ('noncomm_positions_long_all', 'noncomm_positions_short_all', 'open_interest_all'):
        df[c] = df[c].astype(float)
    df['net'] = df.noncomm_positions_short_all - df.noncomm_positions_long_all
    print(f'  CFTC: {len(df)} 週，最新 {df.d.iloc[-1].date()} 淨空 {int(df.net.iloc[-1]):,}')
    return df

# ─────────── 6. 日本本邦資金流（財務省 週報） ───────────
def fetch_jp_flow():
    try:
        r = get('https://www.mof.go.jp/policy/international_policy/reference/'
                'itn_transactions_in_securities/week.csv')
        rows = list(csv.reader(io.StringIO(r.content.decode('cp932', errors='replace'))))
        data = [x for x in rows if x and re.match(r'^\s*\d{4}．', x[0])]
        if not data:
            warn('日本週次資金流解析為空'); return None
        last = data[-1]
        def num(i):
            try: return int(last[i].replace(',', '').strip() or 0)
            except Exception: return None
        out = {'period': last[0].strip(), 'outward_total': num(11), 'inward_total': num(22),
               'outward_bond': num(6), 'inward_bond': num(17), 'weeks': len(data)}
        print(f"  日本資金流: {out['period']} 對內合計 {out['inward_total']}")
        return out
    except Exception as e:
        warn(f'日本週次資金流失敗：{str(e)[:60]}'); return None

# ─────────── 7. 日本核心 CPI（e-Stat） ───────────
def fetch_jp_cpi():
    try:
        r = get('https://www.e-stat.go.jp/stat-search/file-download'
                '?statInfId=000032103932&fileKind=1')
        rows = list(csv.reader(io.StringIO(r.content.decode('cp932', errors='replace'))))
        hdr = rows[0]; col = next((i for i, h in enumerate(hdr) if '生鮮食品を除く総合' in h), None)
        if col is None:
            warn('e-Stat 核心 CPI 欄位未找到'); return None
        for row in reversed(rows[4:]):
            if row and re.match(r'^\d{6}$', row[0].strip()):
                try:
                    v = float(row[col]); ym = row[0].strip()
                    print(f'  日本核心CPI: {ym[:4]}-{ym[4:]} {v:+.1f}%')
                    return {'ym': f'{ym[:4]}-{ym[4:]}', 'yoy': v}
                except ValueError:
                    continue
    except Exception as e:
        warn(f'e-Stat CPI 失敗：{str(e)[:60]}')
    return None

# ─────────── 8. 日本貿易收支（財務省） ───────────
def fetch_jp_trade():
    try:
        r = get('https://www.customs.go.jp/toukei/suii/html/data/d41ma.csv')
        rows = list(csv.reader(io.StringIO(r.content.decode('shift_jis', errors='replace'))))
        best = None
        for row in rows:
            if not row or not re.match(r'^\d{4}/\d{1,2}$', row[0].strip()): continue
            try:
                ex, im = float(row[1]), float(row[2])
            except (IndexError, ValueError):
                continue
            if ex > 0: best = (row[0].strip(), ex, im)
        if best:
            bal = (best[1] - best[2]) / 1e6   # 千円 → 十億円
            print(f'  日本貿易: {best[0]} 收支 {bal:+.1f} 十億円')
            return {'ym': best[0], 'balance_bn': round(bal, 1)}
    except Exception as e:
        warn(f'日本貿易統計失敗：{str(e)[:60]}')
    return None

# ─────────── 9. 財務省介入實績（月次頁 + 日次全史） ───────────
def fetch_intervention():
    out = {'last_event': None, 'last_amount_oku': None, 'events': None}
    try:
        r = get('https://www.mof.go.jp/policy/international_policy/reference/feio/'
                'foreign_exchange_intervention_operations.csv')
        txt = r.content.decode('shift_jis', errors='replace')
        rows = list(csv.reader(io.StringIO(txt)))[14:]
        MON = {'Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'}
        year, evts = None, []
        for row in rows:
            if len(row) < 7: continue
            if re.fullmatch(r'\d{4}', (row[3] or '').strip()): year = row[3].strip()
            mon, day = (row[4] or '').strip()[:3], (row[5] or '').strip()
            if mon in MON and day.isdigit() and year:
                try: evts.append((f'{year}-{mon}-{day}', int((row[6] or '0').replace(',', ''))))
                except ValueError: pass
        if evts:
            out.update(last_event=evts[-1][0], last_amount_oku=evts[-1][1], events=len(evts))
            print(f'  介入實績: {len(evts)} 事件日，最新 {evts[-1][0]} {evts[-1][1]:,} 億円')
    except Exception as e:
        warn(f'介入實績（日次）失敗：{str(e)[:60]}')
    return out

# ─────────── 統計工具 ───────────
def z(s, w=252):
    s = pd.Series(s).dropna()
    if len(s) < 30: return None
    t = s.tail(w)
    sd = t.std()
    return None if not sd or math.isnan(sd) else round(float((s.iloc[-1] - t.mean()) / sd), 2)

def main():
    print('▶ JPY/TWD 資料抓取開始')
    rates, rate_src = fetch_rates()
    bot   = fetch_bot()
    jgb   = fetch_jgb()
    us2, us10 = fetch_fred('DGS2'), fetch_fred('DGS10')
    cftc  = fetch_cftc()
    flow  = fetch_jp_flow()
    cpi   = fetch_jp_cpi()
    trade = fetch_jp_trade()
    itv   = fetch_intervention()

    j = rates['JPYTWD'].dropna()
    anchor = float(j.iloc[-1]); adate = str(j.index[-1].date())
    usdjpy = float(rates['USDJPY'].dropna().iloc[-1])
    usdtwd = float(rates['USDTWD'].dropna().iloc[-1])
    synth  = usdtwd / usdjpy

    # 牌告：抓不到就用中價 × 上次已知加價率
    prev = {}
    p = DATA / 'latest.json'
    if p.exists():
        try: prev = json.loads(p.read_text(encoding='utf-8'))
        except Exception: pass
    if bot['ok']:
        spot_sell, cash_sell = bot['jpy_spot_sell'], bot['jpy_cash_sell']
        spot_buy,  cash_buy  = bot['jpy_spot_buy'],  bot['jpy_cash_buy']
        board_src = '台銀牌告（自動抓取）'
    else:
        pb = (prev.get('board') or {})
        up_s = pb.get('markup_spot', 0.0123); up_c = pb.get('markup_cash', 0.0171)
        spot_sell, cash_sell = round(synth*(1+up_s), 4), round(synth*(1+up_c), 4)
        spot_buy = cash_buy = None
        board_src = '中價 × 上次加價率推估（台銀未取得）'

    # 分位定位
    pctile, rng = {}, {}
    for k, n in (('y1', 252), ('y3', 756), ('y5', 1260), ('all', len(j))):
        s = j.tail(n)
        pctile[k] = round(float((s < anchor).mean()*100), 1)
        rng[k] = [round(float(s.min()), 5), round(float(s.max()), 5)]
    lo_date = str(j.idxmin().date())

    # conformal 區間（隨機漫步為中心，26 週窗）
    lw = np.log(j.resample('W-FRI').last().dropna())
    conf = {}
    for h in (1, 2, 4):
        res = lw.diff(h).dropna().abs().tail(26)
        if len(res) < 10: continue
        hw = float(np.quantile(res, min(1.0, (len(res)+1)/len(res)*0.80)))
        conf[str(h)] = {'hw_pct': round(hw*100, 3),
                        'lo': round(anchor*math.exp(-hw), 5), 'hi': round(anchor*math.exp(hw), 5)}

    # 兩腿貢獻
    lr = np.log(rates[['JPYTWD','USDTWD','USDJPY']].dropna()).diff()
    def legs(n):
        s = lr.tail(n).sum()
        return {'total': round(s.JPYTWD*100, 2), 'twd': round(s.USDTWD*100, 2),
                'jpy': round(-s.USDJPY*100, 2),
                'resid': round((s.JPYTWD - s.USDTWD + s.USDJPY)*100, 2)}

    # 利差
    sp2 = sp10 = None; jgb2 = float(jgb.y2.iloc[-1]); jgb10 = float(jgb.y10.iloc[-1])
    if len(us2):
        a = (us2.reindex(jgb.index).ffill() - jgb.y2).dropna()
        b = (us10.reindex(jgb.index).ffill() - jgb.y10).dropna()
        sp2  = {'val': round(float(a.iloc[-1]), 3), 'z': z(a, 504)}
        sp10 = {'val': round(float(b.iloc[-1]), 3), 'z': z(b, 504)}

    vol1y = round(float(np.log(j).diff().tail(252).std()*math.sqrt(252)*100), 2)
    wr = np.log(j.resample('W-FRI').last().dropna()).diff().dropna()

    out = {
      'generated_at': dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime('%Y-%m-%d %H:%M'),
      'anchor_date': adate, 'anchor': round(anchor, 5),
      'usdjpy': round(usdjpy, 3), 'usdtwd': round(usdtwd, 4),
      'synth': round(synth, 5), 'rate_src': rate_src,
      'need': NEED,
      'board': {'src': board_src, 'ok': bot['ok'], 'quoted_at': bot['quoted_at'],
                'spot_sell': spot_sell, 'cash_sell': cash_sell,
                'spot_buy': spot_buy, 'cash_buy': cash_buy,
                'usd_spot_sell': bot['usd_spot_sell'], 'usd_spot_buy': bot['usd_spot_buy'],
                'markup_spot': round(spot_sell/synth - 1, 5),
                'markup_cash': round(cash_sell/synth - 1, 5)},
      'pctile': pctile, 'range': rng, 'low_date': lo_date,
      'conf': conf, 'legs5': legs(5), 'legs20': legs(20),
      'vol1y': vol1y,
      'week_std': round(float(wr.std()*100), 3), 'week_ac1': round(float(wr.autocorr(1)), 4),
      'rates': {'us2': (round(float(us2.iloc[-1]),2) if len(us2) else None),
                'us10': (round(float(us10.iloc[-1]),2) if len(us10) else None),
                'us_date': (str(us2.index[-1].date()) if len(us2) else None),
                'jgb2': jgb2, 'jgb10': jgb10, 'jgb_date': str(jgb.index[-1].date()),
                'jgb2_chg4w': round(jgb2 - float(jgb.y2.iloc[-21]), 3),
                'spread2': sp2, 'spread10': sp10},
      'cftc': {'date': str(cftc.d.iloc[-1].date()),
               'long': int(cftc.noncomm_positions_long_all.iloc[-1]),
               'short': int(cftc.noncomm_positions_short_all.iloc[-1]),
               'net': int(cftc.net.iloc[-1]), 'oi': int(cftc.open_interest_all.iloc[-1]),
               'z': z(cftc.net, 104),
               'chg2w': int(cftc.net.iloc[-1] - cftc.net.iloc[-3]),
               'notional_tn_jpy': round(cftc.net.iloc[-1]*12.5e6/1e12, 3)},
      'jp': {'flow': flow, 'cpi': cpi, 'trade': trade, 'intervention': itv},
      'series': [[str(i.date()), round(float(v), 5)] for i, v in j.tail(60).items()],
      'warnings': WARN,
    }
    (DATA/'latest.json').write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    (DATA/'history'/f'{adate}.json').write_text(json.dumps(out, ensure_ascii=False), encoding='utf-8')
    print(f'✔ 完成 → data/latest.json（警示 {len(WARN)} 則）')

if __name__ == '__main__':
    main()
