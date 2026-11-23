/* 畫線的存檔（個股頁）。圖表每次通知畫線有變動（新增、移動、改文字、刪除），就存到使用者自己的帳號底下；
   存不上去（斷線、被限速、伺服器暫時出錯、一直沒有回應）時留著，之後自動補存。
   頁面只透過 create() 回傳的 open／merge／record 與 onStatus 往來，排隊、重試、等多久都封在這裡。
   另外自己聽三個瀏覽器事件：連線恢復與分頁回到前景（馬上再試）、要離開頁面（還有沒存上去的就擋下預設行為，
   請瀏覽器先提醒——頁面的其他離開處理可以用 event.defaultPrevented 知道使用者可能留下來）。 */
(function () {
  'use strict';
  const RETRY_MS = [2000, 4000, 8000, 16000, 30000];   // 連續失敗時，下一次隔多久再試（到最後一個為止）
  const TIMEOUT_MS = 10000;                            // 送出去這麼久沒有回應就放棄這一次
  const MAX_WAIT_MS = 300000;                          // 伺服器要求等再久，最多等這麼久

  // opts：fetch、setTimeout、clearTimeout（省略時用瀏覽器的）；
  //   onStatus({ unsaved, failed, ids })：還沒存上去的情況有變時通知頁面——
  //   unsaved 還在等補存的變動數，failed 被拒絕、不會再試的變動數，ids 目前這一檔還沒存上去的線。
  function create(opts) {
    const options = opts || {};
    const request = options.fetch || ((url, init) => window.fetch(url, init));
    const later = options.setTimeout || ((run, ms) => setTimeout(run, ms));
    const cancel = options.clearTimeout || (id => clearTimeout(id));
    // 等著存的變動，照第一次變動的先後，一條線一筆（再改就換成最後的樣子，位置不變）：
    //「代號/識別碼」→ { code, id, drawing（最後的樣子；刪除是 null）, rev（改過幾次）,
    //   fresh（排進來的時候這條線是剛畫的、伺服器還沒有）, sent（這一筆送出去過，伺服器可能已經收下） }
    const queue = new Map();
    // 每一檔在這次瀏覽中畫過、拖過、改過（items：識別碼 → 最後的樣子）與刪過（removed）的線。
    // 雲端那一份晚一步讀回來時以這裡為準，不能把剛做的變動蓋掉，也不能把剛刪的線帶回來。
    const edits = new Map();
    const keyOf = (stock, id) => `${stock}/${id}`;
    const editsOf = stock => {
      if (!edits.has(stock)) edits.set(stock, { items: new Map(), removed: new Set() });
      return edits.get(stock);
    };
    // 被拒絕、不會再試的變動（輸入不合法、超過上限、沒有權限）：「代號/識別碼」→ { code, id, drawing }。
    // 那條線再被改動、被刪掉就不再算；下一次換股票時全部不再算（換了股票之後才被拒絕的，會算到再下一次換股票）。
    const rejected = new Map();
    let code = '';              // 目前看的這一檔
    let busy = false;           // 有一筆正在送
    let stalled = false;        // 從上次全部存完到現在，有沒有存不上去過（有的話，排著的每一筆都算還沒存上去）
    let failures = 0;           // 連續失敗幾次
    let timer = null;           // 等著再試的計時
    let limited = false;        // 這次等的是伺服器要求的時間（被限速）：不能提早
    let reported = JSON.stringify({ unsaved: 0, failed: 0, ids: [] });

    function report() {
      const waiting = stalled ? [...queue.values()] : [];
      const status = {
        unsaved: waiting.length, failed: rejected.size,
        ids: [...rejected.values(), ...waiting].filter(entry => entry.code === code && entry.drawing).map(entry => entry.id),
      };
      const text = JSON.stringify(status);
      if (text === reported) return;
      reported = text;
      if (options.onStatus) options.onStatus(status);
    }

    // 再試也不會過的回應：輸入不合法、超過上限、帳號沒有權限這一類（4xx）。登入過期、逾時、被限速不算——之後有機會過。
    const refused = status => status >= 400 && status < 500 && ![401, 408, 429].includes(status);
    // 送一筆。回傳 { saved: true }、{ refused: true }（被拒絕，再試也不會過）或 { wait }（伺服器要求等幾毫秒再試；沒有要求是 0）。
    // 連不上、太久沒回應會丟出例外。
    async function send(entry) {
      const url = `/api/drawings/${encodeURIComponent(entry.code)}/${encodeURIComponent(entry.id)}`;
      // text 只有文字標註才有；alert（提醒開或關）、ref（現價）、name（股名）只有到價提醒的線才有
      const { kind, anchors, text, alert, ref, name } = entry.drawing || {};
      const abort = new AbortController();
      let giveUp = null;
      const timeout = new Promise((resolve, reject) => {
        giveUp = later(() => { abort.abort(); reject(new Error('timeout')); }, TIMEOUT_MS);
      });
      try {
        const response = await Promise.race([timeout, request(url, {
          // keepalive：送到一半頁面被關掉或切走（手機上不會先問），這一筆也照樣送完。
          method: entry.drawing ? 'PUT' : 'DELETE', cache: 'no-store', keepalive: true, signal: abort.signal,
          ...(entry.drawing ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind, anchors, text, alert, ref, name }) } : {}),
        })]);
        if (response.ok) return { saved: true };
        if (refused(response.status)) return { refused: true };
        // 被限速（或伺服器暫時不收）時照它說的秒數等。
        const seconds = Number(response.headers.get('retry-after'));
        return { saved: false, wait: seconds > 0 ? Math.min(seconds * 1000, MAX_WAIT_MS) : 0 };
      } finally { cancel(giveUp); }
    }

    // 一筆送完才送下一筆：拖完馬上刪的時候，刪除不會比前一筆儲存早到而讓線又冒出來。
    // 存不上去就停在那一筆，等一下從它再試起——後面的不會插隊，補存的先後和當初做的一樣。
    async function pump() {
      if (busy || timer !== null || !queue.size) return;
      const [key, entry] = queue.entries().next().value, rev = entry.rev;
      busy = true; entry.sent = true;
      let result = { wait: 0 };
      try { result = await send(entry); } catch (_) { /* 連不上、太久沒回應 */ }
      busy = false;
      if (result.saved || result.refused) {
        failures = 0;
        // 送的途中又被改過：伺服器看到的是舊的樣子，留著再送一次最後的樣子。
        if (entry.rev === rev) {
          queue.delete(key);
          if (result.refused) rejected.set(key, entry);
          // 不是目前這一檔的：那一檔這次的變動不用再留，回去時以雲端那一份為準。
          if (entry.code !== code) forget(entry);
        }
        if (!queue.size) stalled = false;
      } else {
        stalled = true; limited = result.wait > 0;
        timer = later(() => { timer = null; pump(); }, result.wait || RETRY_MS[Math.min(failures, RETRY_MS.length - 1)]);
        failures += 1;
      }
      report();
      pump();
    }

    function forget({ code: stock, id }) {
      const { items, removed } = editsOf(stock);
      items.delete(id); removed.delete(id);
    }
    // 離開一檔：只留還等著補存的變動（回來時要看得到）；存上去的以雲端為準。
    // 被拒絕的到換股票為止，不管是哪一檔的都不再算。
    function leave(stock) {
      const { items, removed } = editsOf(stock);
      for (const id of [...items.keys(), ...removed]) if (!queue.has(keyOf(stock, id))) forget({ code: stock, id });
      rejected.clear();
    }

    // 連線恢復、分頁回到前景：正在等再試的話不等了，馬上試。被限速的等待是伺服器要求的，不提早。
    function retryNow() {
      if (timer === null || limited) return;
      cancel(timer); timer = null;
      pump();
    }
    window.addEventListener('online', retryNow);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) retryNow(); });
    // 還有沒存上去的變動（含正在送的）就要離開或重新整理：請瀏覽器先提醒。
    window.addEventListener('beforeunload', event => {
      if (!queue.size) return;
      event.preventDefault();
      event.returnValue = '';   // 舊版瀏覽器看這個
    });

    return {
      // 換到這一檔。回傳這一檔這次畫過、還留著的線（雲端那一份讀回來之前先顯示）。
      open(next) {
        if (next !== code) {
          if (code) leave(code);
          code = next;
          report();
        }
        return [...editsOf(code).items.values()];
      },
      // 雲端讀回來的這一檔的線，套上這次瀏覽的變動：雲端的依原本的順序，雲端還沒有的（剛畫、還沒存上去）排在最後。
      merge(cloud) {
        const { items, removed } = editsOf(code), known = new Set(cloud.map(item => item.id));
        return [
          ...cloud.filter(item => !removed.has(item.id)).map(item => items.get(item.id) || item),
          ...[...items.values()].filter(drawing => !known.has(drawing.id)),
        ];
      },
      // 圖表通知的一次變動：{ type: 'add'｜'update'｜'remove', drawing }。
      record(change) {
        if (!code) return;
        const id = change.drawing.id, removing = change.type === 'remove', { items, removed } = editsOf(code);
        if (removing) { items.delete(id); removed.add(id); } else items.set(id, change.drawing);
        const key = keyOf(code, id), waiting = queue.get(key), drawing = removing ? null : change.drawing;
        rejected.delete(key);   // 再動它就重新送一次
        if (!waiting) queue.set(key, { code, id, drawing, rev: 0, fresh: change.type === 'add', sent: false });
        // 這次才畫、還沒送出去過的線又被刪掉：伺服器從頭到尾不知道它，兩筆都不用送。
        else if (removing && waiting.fresh && !waiting.sent) queue.delete(key);
        else { waiting.drawing = drawing; waiting.rev += 1; }
        report();
        pump();
      },
    };
  }

  window.DrawingSync = { create };
})();
