# 國際新聞來源與運用

日期：2026-11-05。內部資料來源／限制備忘，不是面向使用者的報告；實際呈現交由網頁工具的一分鐘盤勢與重要新聞區。目標主來源為 `ANALYSIS_PLAN.md`，方法讀 `MARKET_METHOD.md`。

## 本輪已實測

`logs/news-source-probe-20261105.json`於2026-11-05T13:14:11–15Z記錄一次直接HTTPS讀取及XML解析，沒有常駐監控：

| 用途 | 來源 | 結果與限制 |
| --- | --- | --- |
| 美國貨幣政策原文 | https://www.federalreserve.gov/feeds/press_monetary.xml | HTTP200、15項，有ETag／Last-Modified。最近一筆本輪所見為10/28聲明；不因此判作延遲或誤稱11/05新決策。 |
| 美國經濟資料入口 | https://www.bls.gov/feed/bls_latest.rss | HTTP200、1項，有ETag／Last-Modified。這是最新數字彙總入口，不是每個CPI／非農逐條快訊，仍需對照官方日曆及原發布。 |
| 國際政經事件 | https://feeds.bbci.co.uk/news/world/rss.xml | HTTP200、20項，可取標題／原文連結／pubDate。非完整全球事件覆蓋，排序不等於精確事件時序。 |
| 商業與經濟 | https://feeds.bbci.co.uk/news/business/rss.xml | HTTP200、48項，可取發布時間；本輪看見11/05報導，不代表已量測端到端送達延遲。 |

官方入口說明：
- Fed：https://www.federalreserve.gov/feeds/feeds.htm
- BLS：https://www.bls.gov/help/hlp_rss.htm
- BBC：https://www.bbc.co.uk/news/10628494

另實跑既有Firecrawl Alexandria的`cnbc-com/news/search_news`，query=`Federal Reserve`、sort=`date`、format=`Articles`、page_size=2，返回2則、上游200，帶原文URL、發布及修改時間。requestId=`84afcadd-3740-43ba-9904-b6321446c137`，本次creditsCost=5。只證明主題搜尋可用，不證明搜尋索引即時或完整；部分date_modified早於date_published，需標記資料品質疑義，不能據此斷定事件先後。CNBC RSS介紹頁的Firecrawl直取曾遇`ERR_TUNNEL_CONNECTION_FAILED`，不得把它寫成CNBC RSS已實測成功。

## 建議來源架構（尚未部署）

- 以公開RSS及官方發布為持續來源，候選約5分鐘檢查一次；實際頻率遵循來源政策、Cache-Control、ETag、Last-Modified及Retry-After，不能任意加密抓取。
- 官方經濟日曆定出FOMC、CPI、就業等檢查窗口；數字與政策以官方原文優先，不從二手標題推算市場預期或數值。
- 重大事件才補做既有搜尋／原文交叉核對。不要把本次需5點的CNBC查詢當作免費每5分鐘無限輪詢；未定額度與預算前不啟用自動查詢。
- 記錄來源、canonical URL、主題／事件鍵、發生時間、發布時間、修改時間、首次取得時間、分析截止時間及資料品質旗標。舊聞更新、重複轉載及同一事件多篇報導須合併但保留來源。
- 只保留分析所需的標題、必要事實摘要、原文連結與時間；遵守使用條款，不搬付費全文、不繞登入或付費牆，不把來源可讀視為全文再散布許可。

## 接到分析與網頁的方式

新聞 → 核實事件／時點 → 利率、能源成本、企業獲利、匯率或風險偏好等可能傳導 → 與當期價量／籌碼交叉檢視 → 判斷是否需要修訂主情境。

事實、助手的傳導推論、尚待行情確認的影響分開。利多或利空標籤不是預測；不能從發布後的報導倒推它造成發布前的台股漲跌。網頁要顯示新聞發布與取得時間、對盤勢的可能影響及何時失效，而不是一串標題。

本輪可引用的兩個事件及時間限制保存在`data/news-snapshots/20261105/events.json`，供原工作線的一分鐘稿／網頁使用。該檔是一次查證快照，不是持續更新中的新聞庫。

## 即時性的誠實界線

目前已完成可讀性測試及原文查證，未啟排程、常駐程序、推播或付費串流。5分鐘是候選檢查間隔，不是端到端延遲承諾；來源本身更新、快取、索引及取稿都可能增加延遲。零延遲、秒級完整快訊通常另需有授權的資料服務，本案未選定、未開通。

已保存的兩則事件不是當天台股跌幅的因果證明；當期行情判斷與一分鐘稿仍由原工作線負責，本包不重寫其稿件。
