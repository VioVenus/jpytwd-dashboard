# 日圓換匯 決策儀表板（JPY/TWD）

個人換匯規劃的輔助工具。單一靜態網頁 + 每日自動更新，全部使用免費公開資料源。

**不預測匯率。** 只做三件事：告訴你現在的價格在什麼位置、正常情況下會晃多大、哪些日子和哪些數字該盯。
理由見同層目錄的《JPY/TWD 儀表板 評估規劃報告》第二章——同一套預測方法在 USD/TWD 上驗證過，上線後兩週與四週方向猜中率為 0。

---

## 架構

```
index.html                     儀表板（讀 data/latest.json 渲染）
data/latest.json               最新資料（由 workflow 產生）
data/history/YYYY-MM-DD.json   每日快照
scripts/fetch_data.py          資料抓取
.github/workflows/update.yml   排程 + 手動觸發 + 部署 Pages
```

`index.html` 完全由 `data/latest.json` 驅動，改資料不用改頁面。

---

## 建置步驟

### 1. 建 repo 並推上去

```bash
cd jpytwd-dashboard
git init -b main
git add .
git commit -m "初始版本"
git remote add origin https://github.com/<你的帳號>/jpytwd-dashboard.git
git push -u origin main
```

### 2. 改 `index.html` 裡的 repo 名稱

第一行 script 設定區：

```js
const REPO = "YOUR_GITHUB_USER/jpytwd-dashboard";   // ← 改成你的
```

這是「▶ 重跑完整更新」按鈕要連到的 Actions 頁面。

### 3. 設定 Secrets 與 Variables

**Settings → Secrets and variables → Actions**

| 類型 | 名稱 | 值 | 必要性 |
|---|---|---|---|
| Secret | `FRED_API_KEY` | [免費申請](https://fred.stlouisfed.org/docs/api/api_key.html)，一分鐘 | 建議（缺了只是少美債利率） |
| Variable | `RUNNER_LABEL` | `self-hosted` 或 `ubuntu-latest` | 選填，預設 `self-hosted` |
| Variable | `JPY_TARGET` | `4000000` | 選填，預設 400 萬 |

### 4. 開啟 GitHub Pages

**Settings → Pages → Source** 選 **GitHub Actions**。

### 5. 設定 runner（重要）

**台銀、證交所、央行三個來源會擋境外 IP**，回傳一頁空白。所以：

- **有台灣的機器** → 裝 self-hosted runner（Settings → Actions → Runners → New self-hosted runner），`RUNNER_LABEL` 留 `self-hosted`。台銀牌告正常抓。
- **沒有** → `RUNNER_LABEL` 設 `ubuntu-latest`。其餘來源全部正常，只有台銀牌告改用「中價 × 上次已知加價率」推估，頁面會標示為「推估中」，你也可以直接在頁面第 2 區手動填入實際牌告。

### 6. 跑第一次

**Actions → 每日更新匯率資料 → Run workflow**。跑完 Pages 會自動部署到
`https://<你的帳號>.github.io/jpytwd-dashboard/`

---

## 更新機制

| 方式 | 觸發 | 範圍 | 耗時 |
|---|---|---|---|
| 排程 | 台北 09:30 / 17:30，週一至五 | 全部來源 | 1–2 分鐘 |
| 頁面「▶ 重跑完整更新」 | 手動（連到 Actions） | 全部來源 | 1–2 分鐘 |
| 頁面「⟳ 更新即時匯率」 | 手動（純前端） | 只有美元兌日圓／美元兌台幣及台幣金額 | 幾秒 |

「⟳ 更新即時匯率」在瀏覽器裡直接抓 CORS 開放的免費 API（open.er-api → currency-api → frankfurter 三源依序備援），不需要後端，適合盤中查價。

---

## 資料源

全部免費、公開，無付費服務。

| 資料 | 來源 | 金鑰 | 境外 IP |
|---|---|---|---|
| 台銀牌告（即期／現金 買賣） | rate.bot.com.tw | 免 | **需台灣** |
| 匯率中價與歷史 | Yahoo Finance（備援 frankfurter + open.er-api） | 免 | 可 |
| 日本公債全曲線 | 財務省 `jgbcm_all.csv` | 免 | 可 |
| 美國公債 2Y／10Y | FRED `DGS2` `DGS10` | 免費申請 | 可 |
| 投機部位（日圓淨空） | CFTC Socrata | 免 | 可 |
| 日本對外／對內證券投資（週） | 財務省 `week.csv` | 免 | 可 |
| 日本核心 CPI | e-Stat | 免 | 可 |
| 日本貿易收支 | 財務省 customs | 免 | 可 |
| 干預實績 | 財務省 feio | 免 | 可 |

解析上的坑（都已處理）：財務省 CSV 是 Shift-JIS 且日期為和曆（`R8.7.31`）；e-Stat 回應 header 寫 UTF-8 但實際是 cp932；貿易統計會預先列出未來月份且值為 0，須過濾；週次資金流的數字含千分位逗號，必須用 `csv.reader`。

---

## 本機測試

```bash
pip install -r requirements.txt
FRED_API_KEY=你的金鑰 python scripts/fetch_data.py
python -m http.server 8000        # 開 http://localhost:8000
```

`fetch_data.py` 任何來源失敗都不會中斷，會寫進 `warnings` 陣列並顯示在頁面頂端。

---

## 調整參數

| 想改什麼 | 改哪裡 |
|---|---|
| 換匯總金額 | Variable `JPY_TARGET`，或 `fetch_data.py` 的 `NEED` 預設值 |
| 加碼／止血觸發價 | `index.html` 的 `BUY_TRIG` / `STOP_TRIG` |
| 批次金額與期限 | `index.html` 的 `BATCH` 陣列 |
| 事件日曆 | `index.html` 第 5 區的表格（目前為靜態，未來可移到 JSON） |

---

## 免責

本頁為個人換匯規劃的輔助工具，**不是投資建議**。行情情境引用《美日干預日圓深度分析 2026-08-03》後機械換算，計算時把美元兌台幣固定在單一水準，未計入台幣本身的波動（台幣兩個月的正常波動就可能達 ±2.2%，足以抵銷或放大結論）。匯率涉及重要財務決定，請諮詢往來銀行與合格財務顧問。製作者非財務顧問。
