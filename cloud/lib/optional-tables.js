// 可以晚一步才套到資料庫的名錄表（0018 的 etf_securities、0026 的 fund_securities）：
// 查詢用到的表還不存在時，拿掉用到它的那一段（parts 裡帶 table 的那一筆）再查一次，其他照常。
// query(kept) 用留下來的那幾段組查詢並回傳結果；不是「表不存在」的錯誤照樣丟出去。
export async function withoutMissingTables(parts, query) {
  const kept = [...parts];
  for (;;) {
    try {
      return await query(kept);
    } catch (error) {
      const missing = kept.findIndex((part) => part.table && String(error?.message).includes(`no such table: ${part.table}`));
      if (missing < 0) throw error;
      kept.splice(missing, 1);
    }
  }
}
