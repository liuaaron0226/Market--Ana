# AGENTS.md

Market--Ana 是一個台股分析網站（Cloudflare Pages＋D1＋R2，Python 資料管線，GitHub Actions 排程，AI 只用 Gemini）。

接手維護前先讀 [`docs/operations/for-ai-maintainers.md`](docs/operations/for-ai-maintainers.md)：倉庫地圖、怎麼跑測試、已知失敗基線、不能破壞的規矩。營運總覽在 [`docs/operations/README.md`](docs/operations/README.md)，進行中的工作在 [`docs/operations/handover.md`](docs/operations/handover.md)。

最重要的三條：倉庫是公開的（log 與提交不得出現信箱、持股代號、金鑰）；推上 `main` 等於上線，推送、部署、正式資料庫、開關都先問站主；跟站主一律用繁體中文。
