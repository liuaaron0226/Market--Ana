# Market--Ana

公開頻道 `https://www.youtube.com/@jinjason999` 的直播證據分析。先交有來源的紀錄，再做網頁判讀工具；不自動交易、不發布內容。

## 先看結果

- [本輪狀態](STATUS.md)
- [EP124 HTML 報告（離線截圖已內嵌）](reports/EP124-首次真實試跑.html)
- [原始 Markdown 紀錄](reports/EP124-首次真實試跑.md)
- [規格與限制](ANALYSIS_PLAN.md)
- [工具檢查及雙軸審查](reports/工具驗證與審查.md)

本輪已取得一支完整真實直播及其影音、轉錄和截圖。正式整場分析仍為0/20；機器轉錄不是已驗證逐字稿，未核對區段全部保留。

## 執行環境

目前本機位置 `C:/Users/User/Desktop/Market--Ana`。Python 3.11+，既有 yt-dlp、FFmpeg、ffprobe，建議 Node 供 YouTube JavaScript runtime。Python 套件為原已安裝的 yt-dlp、requests，未安裝新框架。

無字幕時使用本機既有 `/watch` Groq 設定，明確指定中文轉錄。可透過 `GROQ_API_KEY` 或既有 `~/.config/watch/.env` 提供金鑰；不要寫進專案或回覆。若未能自動找到 helper，可用 `--watch-scripts` 或 `WATCH_SCRIPTS` 指定既有 watch 的 scripts 目錄。沒有金鑰／helper 時保留待轉錄，不要求重設已發布環境。

`--transcribe` 會把公開影片的抽出音訊送到 Groq，不是本地辨識。單次目前限24 MiB；超過會停下，不悄悄截斷或把部分轉錄當整場成功。本輪約18.64 MiB，已實測。後續長片分塊與品質複核須先補足，不能直接當成熟批次工具。

## 指令

在本專案目錄執行：

```sh
python analysis.py discover --lives 1
python analysis.py sample --url "https://www.youtube.com/watch?v=G4vcmeBFDm0" --transcribe
python analysis.py capture --video-id G4vcmeBFDm0 --timestamps "205,820,978,1598"
python analysis.py validate --video-id G4vcmeBFDm0
python analysis.py progress
python -m unittest -v
```

只在確認要下載或轉錄時才執行 `sample`。`discover` 只讀取清單及保存metadata，不下載影音；本輪未啟動其他影片。

`sample` 只接受此頻道已結束回放，保留原片、時長與雙軌探測。重跑可復用已取得的完整原片及轉錄，仍檢查資料邊界。新增截圖以原片 SHA-256 前綴分目錄，索引保留原片秒數、圖片及來源版本雜湊。

`validate` 驗原片／音訊時長與版本、轉錄來源時間、必要欄位、截圖解碼與索引、分段連續性。它不證明內容正確、語音準確或策略有效。

## 資料與證據

`data/<video_id>/` 保留 `source.mkv`、`source.info.json`、`probe.json`、`audio.mp3`、`transcript.json`、`transcript.zh.raw.json`、`transcript.txt`、`frames.json`、`records.json`、`segments.json`。

- 原片連結一律用原片秒數，不用剪輯後秒數。
- 截圖以原生解析度擷取。文字與參數若未顯示，明寫「未知」，不套用預設值。
- `review-clips/` 僅為人工回看的含聲音連續摘錄，`review-clips.json` 記錄原片起點；不是非股票排除結果，原片保留不變。
- `segments.json` 在全片尚未核對時保留整片 `uncertain`。未確認的閒聊、總經、指數及上下文不可直接刪除。
- 本輪 `transcript.json` 有5段疑似幻覺警示，不能當確定原話；燒錄AI字幕同樣要核對。

資料及日誌因體積與來源網址等資訊列入 `.gitignore`；本機檔案仍在，並非已推送或已備份到GitHub。重新clone拿不到本輪未提交工具或素材。搬交工作區時需一併保留工具、狀態、報告及所需data，不可只交一段摘要。

## 尚未做

批次處理、其餘19場以上深度分析、Shorts、全片非股票分類／排除、網頁、交易判讀、自動交易、公開網址。沒有提交、推送或部署。
