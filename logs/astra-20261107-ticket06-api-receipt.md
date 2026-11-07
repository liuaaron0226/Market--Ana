# Astra r1 回執（票 06 市場資料 API 化）

送出：logs/astra-20261107-ticket06-api-prompt.md；回覆：logs/astra-20261107-ticket06-api-r1.json（UTF-8 全文 logs/astra-20261107-ticket06-api-r1.md；session ee234ee0，modelUsage=claude-fable-5-dd-artsa-6-tpg，num_turns 1，用 --tools "" --strict-mcp-config）。

Astra 結論：草案不宜原樣通過。改採「Functions 打包＋統一非同步資料介面（本機／雲端兩個 adapter）＋劇本分布後端算＋歷史 K 線分段＋下鑽按日期與排行類別＋D1 基本限速」。不改 live_server.py、不先引入 KV／R2。

採納：
- 抓到草案錯誤：劇本分布 calculateReturnRows 用全量 daily＋institutional（外資賣超條件），草案把 institutional 截成 20 筆會改變樣本。改為劇本計算抽成共用純函式，雲端在後端算、只回分布與樣本數。
- 「只給畫面需要的資料」與一次給 2016 起全量日線矛盾；要嘛分段，要嘛本人明確改驗收文字。→ 交本人決定（一題）。
- 下鑽改按「日期＋排行類別」，一次只回一張表。
- 前端以統一非同步介面接縫；撤回「minimal.js 不改」的承諾；處理舊請求覆蓋新選擇、並行合併、失敗≠沒資料、版本切換清快取。
- 所有 API 帶同一個 data_version，版本不符要求重新載入。
- D1 限速：原子條件更新、超限不再寫、異常事件去重、限速器故障回 503（與 429 分開）。數值改為：全部市場 API 每人每分 30 次；首頁資料獨立額度；歷史分頁／下鑽每分 10～12 次；記錄每版本取得的不同日期／排行數量作異常偵測，先告警不封號。
- 部署產物採允許清單並掃描無資料檔；舊部署網址要盤點（已下載的收不回）。
- 前端辨識 Access 失效（非 JSON／轉址）顯示「登入已失效」，不無限重試；API 回 `Cache-Control: private, no-store`。
- 欄位允許清單：dashboard／chips／plain 也逐欄確認，不因檔小就全給。
- 驗收補：劇本樣本數與門檻、每種排行、分頁接合與 MA/MACD、429、未核准／撤銷拿不到資料；service token 不取代真人登入過期旅程。

不採納／待定：
- 歷史分頁時 MACD 初始化一致性：若本人選「日線照全量給」則不需要；選分段則採「分段帶後端預算指標」。
- 長時間硬上限與 token bucket：第一版先固定窗＋明示雙倍突發限制。

交接邊界（寫入票 06）：能承諾降低單次曝光、限制大量擷取速度、留下紀錄；不能承諾核准使用者無法逐步重建畫面可見的資料。
