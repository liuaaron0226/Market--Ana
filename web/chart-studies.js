/* 日線副圖（量能、MACD，個股頁另有 KD、RSI）。一次只顯示一個；互動狀態完全沿用主 KChart，不另存日期或範圍。 */
(function () {
  'use strict';

  const DPR = () => Math.max(1, Math.min(3, window.devicePixelRatio || 1));
  const KNOWN_KINDS = ['volume', 'macd', 'kd', 'rsi', 'foreign', 'trust', 'dealer'];
  // 指標名稱（.kc-study-title）疊在指標區左上角：畫圖時上緣留這一條給它，線與柱不會壓到字。
  // 名稱的位置與字級在 chart-studies.css 的 .kc-study-head／.kc-study-title，兩邊要一起改。
  const TITLE_STRIP_PX = 20;
  const TAG_HEIGHT_PX = 18;
  const STUDY_MENU_ID = 'studyMenu';
  // KD 以 K、D 初值 50 遞推，前端只有約 120 根日K：前 9 根算 RSV，之後再暖機 40 根，
  // 讓初值的影響衰減到 0.01 以下，最後一根才會和後端用較長歷史算出的 K9／D9 一致。
  const KD_READY_BARS = 49;
  const RSI_PERIOD = 14;

  // KD(9,3,3)：RSV＝(收−9日最低)/(9日最高−9日最低)×100，高低相同時 50；K＝⅔前K＋⅓RSV；D＝⅔前D＋⅓K。
  function kdSeries(candles) {
    let k = 50, d = 50, count = 0;
    return candles.map((candle, index) => {
      const valid = candle && ['high', 'low', 'close'].every(key => Number.isFinite(candle[key]));
      if (!valid) { k = 50; d = 50; count = 0; return { k: null, d: null, ready: false }; }
      count += 1;
      if (count < 9) return { k: null, d: null, ready: false };
      const window = candles.slice(index - 8, index + 1);
      const high = Math.max(...window.map(c => c.high));
      const low = Math.min(...window.map(c => c.low));
      const rsv = high === low ? 50 : (candle.close - low) / (high - low) * 100;
      k = k * 2 / 3 + rsv / 3;
      d = d * 2 / 3 + k / 3;
      return { k, d, ready: count >= KD_READY_BARS };
    });
  }

  // RSI14 用簡單平均（近 14 個漲跌幅各自加總÷14），不是 Wilder 平滑；全無漲跌時沒有數值。
  function rsiSeries(candles) {
    let count = 0;
    return candles.map((candle, index) => {
      if (!candle || !Number.isFinite(candle.close)) { count = 0; return { rsi: null, ready: false }; }
      count += 1;
      if (count <= RSI_PERIOD) return { rsi: null, ready: false };
      let up = 0, down = 0;
      for (let i = index - RSI_PERIOD + 1; i <= index; i += 1) {
        const move = candles[i].close - candles[i - 1].close;
        if (move > 0) up += move; else down -= move;
      }
      if (up + down === 0) return { rsi: null, ready: true };
      return { rsi: 100 * up / (up + down), ready: true };
    });
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function text(value) {
    return document.createTextNode(value);
  }

  function token(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  // 法人買賣超的單位跟著圖走：大盤（成交金額圖）是億元，個股（成交量圖）是股數、顯示成張。
  function institutionalText(value, volumeKind, compact) {
    if (volumeKind === 'amount') return `${formatNumber(Math.abs(value), compact ? 0 : 1)}${compact ? '億' : ' 億元'}`;
    return `${formatNumber(Math.abs(value) / 1000, 0)}${compact ? '張' : ' 張'}`;
  }

  function finite(value) {
    return Number.isFinite(value);
  }

  function formatNumber(value, maximumFractionDigits = 2) {
    if (!finite(value)) return '缺值';
    return value.toLocaleString('zh-TW', {
      minimumFractionDigits: 0,
      maximumFractionDigits
    });
  }

  function formatPrice(value) {
    return finite(value) ? formatNumber(value, 2) : '缺值';
  }

  function formatMacd(value) {
    return finite(value) ? formatNumber(value, 2) : '缺值';
  }

  function formatSigned(value) {
    if (!finite(value)) return '缺值';
    if (value > 0) return `+${formatNumber(value, 2)}`;
    if (value < 0) return `−${formatNumber(Math.abs(value), 2)}`;
    return formatNumber(0, 2);
  }

  function labelFor(candle) {
    if (!candle) return '日期不明';
    if (candle.label !== undefined && candle.label !== null && String(candle.label).trim()) {
      return String(candle.label);
    }
    if (candle.t !== undefined && candle.t !== null && String(candle.t).trim()) {
      return String(candle.t);
    }
    return '日期不明';
  }

  function addExplanationItem(list, title, body) {
    const item = element('li', 'kc-study-explainer-item');
    item.append(
      element('strong', 'kc-study-explainer-title', title),
      element('p', 'kc-study-explainer-copy', body)
    );
    list.append(item);
    return item;
  }

  function addCell(row, tag, value, scope) {
    const cell = element(tag, '', value);
    if (scope) cell.scope = scope;
    row.append(cell);
    return cell;
  }

  class ChartStudies {
    constructor(chart, host, options = {}) {
      if (!chart || !host) throw new TypeError('ChartStudies 需要 chart 與 host。');
      if (options.volumeKind !== 'amount' && options.volumeKind !== 'volume') {
        throw new TypeError("ChartStudies options.volumeKind 必須是 'amount' 或 'volume'。");
      }

      this.chart = chart;
      this.host = host;
      this.volumeKind = options.volumeKind;
      // 首頁維持量能、MACD；個股頁另加 KD、RSI（本人 11-09）。仍是同一個單槽，一次只顯示一個。
      this.kinds = (Array.isArray(options.studies) && options.studies.length ? options.studies : ['volume', 'macd'])
        .filter(kind => KNOWN_KINDS.includes(kind));
      if (!this.kinds.includes('volume')) this.kinds.unshift('volume');
      this.active = 'volume';
      this._tableKey = '';
      this._candlesRef = null;
      this._candlesVersion = 0;
      this._oscRef = null;
      this.kd = [];
      this.rsi = [];

      this.root = element('section', 'kc-studies');
      this.root.setAttribute('aria-label', '日線副圖');

      this.notice = element('p', 'kc-studies-notice', '日線副圖：切回日K查看');
      this.notice.hidden = true;

      const volumeTitle = this.volumeKind === 'amount' ? '成交金額' : '成交量';
      const volumePane = this._makePane('volume', volumeTitle);
      this.volumePane = volumePane.pane;
      this.volumeReadout = volumePane.readout;
      this.volumeCanvas = volumePane.canvas;

      const macdPane = this._makePane('macd', 'MACD（12／26／9）');
      this.macdPane = macdPane.pane;
      this.macdReadout = macdPane.readout;
      this.macdCanvas = macdPane.canvas;

      this.panes = [this.volumePane, this.macdPane];
      if (this.kinds.includes('kd')) {
        const kdPane = this._makePane('kd', 'KD（9／3／3）');
        this.kdPane = kdPane.pane;
        this.kdReadout = kdPane.readout;
        this.kdCanvas = kdPane.canvas;
        this.panes.push(this.kdPane);
      }
      if (this.kinds.includes('rsi')) {
        const rsiPane = this._makePane('rsi', 'RSI（14）');
        this.rsiPane = rsiPane.pane;
        this.rsiReadout = rsiPane.readout;
        this.rsiCanvas = rsiPane.canvas;
        this.panes.push(this.rsiPane);
      }
      this.institutionalPanes = {};
      const institutionalLabels = { foreign: '外資買賣超', trust: '投信買賣超', dealer: '自營商買賣超' };
      for (const kind of ['foreign', 'trust', 'dealer']) {
        if (!this.kinds.includes(kind)) continue;
        const parts = this._makePane(kind, institutionalLabels[kind]);
        this.institutionalPanes[kind] = parts;
        this.panes.push(parts.pane);
      }

      // 指標的選單：平常收著，點指標區左上角的指標名稱才浮出來（開合在 chart-menus.js）。
      this.switcher = element('div', 'kc-study-menu chart-menu');
      this.switcher.id = STUDY_MENU_ID;
      this.switcher.hidden = true;
      this.switcher.setAttribute('role', 'group');
      this.switcher.setAttribute('aria-label', '切換指標');
      const choiceLabels = { volume: volumeTitle, macd: 'MACD', kd: 'KD', rsi: 'RSI', foreign: '外資', trust: '投信', dealer: '自營商' };
      for (const kind of this.kinds) {
        const button = element('button', 'kc-study-choice', choiceLabels[kind]);
        button.type = 'button'; button.dataset.study = kind; button.dataset.menuClose = '';
        button.addEventListener('click', () => this.select(kind));
        this.switcher.append(button);
      }
      // 指標區排最前面：它接在 K 線正下方、和 K 線同一個圖框（本人 11-14）。
      this.root.append(
        ...this.panes,
        this.switcher,
        this.notice
      );
      this.host.append(this.root);

      if (typeof this.chart.bindCanvas === 'function') {
        for (const pane of this.panes) this.chart.bindCanvas(pane.querySelector('canvas'));
      }
    }

    select(kind) {
      if (!(this.available || this.kinds).includes(kind)) return;
      this.active = kind;
      this.draw();
    }

    // 只開放其中幾種指標（基金當成一般個股 03 票：基金只有淨值，沒有量、高低價與法人，只留 MACD、RSI）；null＝全部。
    // 名單外的指標選單鍵收起、選不到；目前看的指標不在名單時換成名單第一個。
    setAvailable(kinds) {
      this.available = Array.isArray(kinds) ? this.kinds.filter(kind => kinds.includes(kind)) : null;
      const allowed = this.available || this.kinds;
      for (const button of this.switcher.querySelectorAll('[data-study]')) button.hidden = !allowed.includes(button.dataset.study);
      if (!allowed.includes(this.active)) this.active = allowed[0] || this.kinds[0];
      this.draw();
    }


    _exampleItem(list, lead) {
      const item = element('li', 'kc-study-explainer-item');
      item.append(element('strong', 'kc-study-explainer-title', '當日實例'));
      const example = element('p', 'kc-study-explainer-copy');
      const dateNode = element('span', 'kc-study-value', '日期尚無');
      const valueNode = element('span', 'kc-study-value', '資料不足');
      const stateNode = element('span', 'kc-study-value kc-study-value-state', '尚無足夠資料可判讀。');
      example.append(dateNode, text(lead), valueNode, text('。'), stateNode);
      item.append(example);
      list.append(item);
      return { dateNode, valueNode, stateNode };
    }

    _makeKdDetails() {
      const parts = this._makeDetails('KD怎麼看');
      const list = element('ol', 'kc-study-explainer');
      addExplanationItem(list, '意義',
        'KD 看收盤價落在最近 9 天高低區間的哪個位置：越接近 100 代表收在區間高處，越接近 0 代表收在低處。用來觀察短線強弱與轉折，不是買賣保證。');
      addExplanationItem(list, '線條與數值',
        'K 為實線、D 為虛線，數值介於 0 到 100。80 以上常稱高檔、20 以下常稱低檔，圖上以虛線標出。');
      addExplanationItem(list, '參數與計算',
        '採 9／3／3：RSV＝(收盤−9日最低)÷(9日最高−9日最低)×100；K＝⅔前K＋⅓RSV；D＝⅔前D＋⅓K；起始值 50。前 49 根視為暖機，不畫線。');
      addExplanationItem(list, '常見用法',
        'K 由下往上穿越 D 稱黃金交叉，代表短線轉強；由上往下穿越稱死亡交叉。在 20 以下的交叉比在中間位置更有參考性，仍要搭配價格位置與量能。');
      addExplanationItem(list, '失準注意',
        '強勢股可能長時間停在 80 以上（高檔鈍化），弱勢股也可能一直在 20 以下；盤整時交叉會反覆出現。KD 反應快，假訊號也多，不能單獨當作進出依據。');
      const example = this._exampleItem(list, '：');
      parts.body.append(list);
      return { details: parts.details, ...example };
    }

    _makeRsiDetails() {
      const parts = this._makeDetails('RSI怎麼看');
      const list = element('ol', 'kc-study-explainer');
      addExplanationItem(list, '意義',
        'RSI 比較最近 14 天上漲幅度和下跌幅度的大小：上漲占比越大數值越高。用來看買盤力道是否過熱或過冷，不是價格預測。');
      addExplanationItem(list, '線條與數值',
        '單一實線，數值介於 0 到 100。70 以上常稱偏熱、30 以下常稱偏冷，圖上以虛線標出，50 為多空分界。');
      addExplanationItem(list, '參數與計算',
        '採 14 日簡單平均：把最近 14 天的上漲點數相加除以 14、下跌點數相加除以 14，RSI＝上漲平均÷(上漲平均＋下跌平均)×100。這不是常見的 Wilder 平滑，和本站個股分析的 RSI14 是同一個口徑。');
      addExplanationItem(list, '常見用法',
        '在 70 以上要留意追高風險，30 以下要留意是否已超跌；從 30 以下回升或跌破 70 回落，常被當成動能轉向的觀察點。');
      addExplanationItem(list, '失準注意',
        '趨勢強的時候 RSI 可以長時間停在高檔或低檔；單看 RSI 偏熱就賣、偏冷就買，容易逆勢。要和價格結構、量能一起看。');
      const example = this._exampleItem(list, '的 RSI 為 ');
      parts.body.append(list);
      return { details: parts.details, ...example };
    }

    _makePane(kind, titleValue) {
      const pane = element('section', `kc-study-pane kc-study-pane-${kind}`);
      // 指標名稱疊在指標區左上角，它本身就是切換指標的小鍵；讀數留給螢幕閱讀器，樣式把它收起來。
      const header = element('header', 'kc-study-head');
      const titleNode = element('h3', 'kc-study-title');
      const chip = element('button', 'chart-chip', titleValue);
      chip.type = 'button';
      chip.setAttribute('aria-expanded', 'false');
      chip.setAttribute('aria-controls', STUDY_MENU_ID);
      titleNode.append(chip);
      const readout = element('p', 'kc-study-readout sr-only', '尚無日線資料');
      readout.setAttribute('aria-label', `${titleValue}目前選取值`);
      header.append(titleNode, readout);

      const canvas = element('canvas', 'kc-study-canvas');
      canvas.tabIndex = 0;
      canvas.setAttribute('role', 'img');
      canvas.style.touchAction = 'pan-y';
      if (kind === 'volume') {
        const unit = this.volumeKind === 'amount' ? '億元' : '張與股';
        canvas.setAttribute('aria-label', `${titleValue}副圖；日期尚無；單位：${unit}。`);
      } else if (kind === 'macd') {
        canvas.setAttribute('aria-label', 'MACD副圖；日期尚無；單位同主圖價格。');
      } else if (kind === 'kd' || kind === 'rsi') {
        canvas.setAttribute('aria-label', `${titleValue}副圖；日期尚無；數值範圍 0 到 100。`);
      } else {
        canvas.setAttribute('aria-label', `${titleValue}副圖；日期尚無；單位：${this.volumeKind === 'amount' ? '億元' : '張'}。`);
      }

      pane.append(header, canvas);
      return { pane, readout, canvas };
    }

    _makeDetails(summaryText) {
      const details = element('details', 'kc-study-details');
      const summary = element('summary', 'kc-study-details-summary', summaryText);
      const body = element('div', 'kc-study-details-body');
      details.append(summary, body);
      return { details, body };
    }

    _makeVolumeDetails() {
      const isAmount = this.volumeKind === 'amount';
      const parts = this._makeDetails(isAmount ? '成交金額怎麼看' : '成交量怎麼看');
      const list = element('ol', 'kc-study-explainer');

      addExplanationItem(
        list,
        '意義',
        isAmount
          ? '成交金額表示當天市場實際成交的金額規模，用來觀察資金參與程度；它不是漲跌方向，也不是成交股數。'
          : '成交量表示當天實際成交的股票數量，用來觀察參與程度與籌碼交換；量大或量小本身不等於上漲或下跌。'
      );
      addExplanationItem(
        list,
        '線條與數值',
        isAmount
          ? '每根單色柱對應同一交易日，柱高代表成交金額。副圖右側與摘要都以億元標示，並和上方K棒共用日期位置。'
          : '每根單色柱對應同一交易日，柱高代表成交股數。摘要同時列出張與股，1張等於1,000股，並和上方K棒共用日期位置。'
      );
      addExplanationItem(
        list,
        '參數與計算',
        isAmount
          ? '本站直接使用日資料中的成交金額，傳入值已是億元，不再除以一億。高度只依目前可見區間獨立縮放，不和價格或MACD共用縱軸。'
          : '本站使用日資料中的原始股數；顯示張數時以股數除以1,000，股數仍完整保留。高度只依目前可見區間獨立縮放，不和價格或MACD共用縱軸。'
      );
      addExplanationItem(
        list,
        '常見用法',
        '把量能和價格位置一起看：突破時若量能比近期明顯增加，代表參與者增加；回測時量能收斂，常用來檢查賣壓是否減弱。也要和前幾日及同類市場環境比較。'
      );
      addExplanationItem(
        list,
        '失準注意',
        '單日爆量可能來自指數調整、大宗交易、除權息或事件衝擊；低量也可能只是休市前後或流動性不足。量能不能單獨證明趨勢會延續，更不能當成必買或必賣訊號。'
      );

      const exampleItem = element('li', 'kc-study-explainer-item');
      exampleItem.append(element('strong', 'kc-study-explainer-title', '當日實例'));
      const example = element('p', 'kc-study-explainer-copy');
      const dateNode = element('span', 'kc-study-value', '日期尚無');
      const valueNode = element('span', 'kc-study-value', '資料不足');
      example.append(
        dateNode,
        text(isAmount ? '的成交金額為 ' : '的成交量為 '),
        valueNode,
        text('。請再和前幾日柱高及當日價格位置比較；單日放大不代表後續一定同方向。')
      );
      exampleItem.append(example);
      list.append(exampleItem);

      parts.body.append(list);
      return { details: parts.details, dateNode, valueNode };
    }

    _makeMacdDetails() {
      const parts = this._makeDetails('MACD怎麼看');
      const list = element('ol', 'kc-study-explainer');

      addExplanationItem(
        list,
        '意義',
        'MACD比較短期與長期指數移動平均的距離，用來觀察趨勢動能及其變化速度。它是落後於價格的衍生指標，不是對未來報酬的保證。'
      );
      addExplanationItem(
        list,
        '線條與數值',
        'DIF以實線表示，DEA以虛線表示；柱狀值為DIF減DEA。零線上、下及正柱、負柱都有文字或線型提示，不只依靠紅綠顏色辨識。'
      );
      addExplanationItem(
        list,
        '參數與計算',
        '採12／26／9：EMA12與EMA26都以第一個有效收盤價作種子，之後使用α＝2／(期數＋1)遞推；DIF＝EMA12−EMA26，DEA是DIF的EMA9並以第一個有效DIF作種子；柱＝DIF−DEA，不乘2。需34根有效日線完成本站暖機，暖機前不畫成零。個股先用較長歷史暖機，再取畫面區間，服務值保留3位小數、畫面顯示2位；大盤遞推保留原精度，顯示時取2位。資料來源排除缺價格的交易列、不補停牌K棒；若收到明確的缺價格列，線段中斷並重新暖機。'
      );
      addExplanationItem(
        list,
        '常見用法',
        '常見觀察包含DIF與DEA的相對位置、兩線是否在零線上方，以及柱狀體連續擴大或收斂。應同時確認價格結構、量能和所在位置，而不是只看一次交叉。'
      );
      addExplanationItem(
        list,
        '失準注意',
        '盤整時交叉可能反覆出現，急漲急跌時也會因EMA落後而太晚反應。金叉不代表必買、死叉不代表必賣；資料缺值或未滿34根有效日線時，不應硬做訊號判讀。'
      );

      const exampleItem = element('li', 'kc-study-explainer-item');
      exampleItem.append(element('strong', 'kc-study-explainer-title', '當日實例'));
      const example = element('p', 'kc-study-explainer-copy');
      const dateNode = element('span', 'kc-study-value', '日期尚無');
      const difNode = element('span', 'kc-study-value', '資料不足');
      const deaNode = element('span', 'kc-study-value', '資料不足');
      const histNode = element('span', 'kc-study-value', '資料不足');
      const stateNode = element('span', 'kc-study-value kc-study-value-state', '尚無足夠資料可判讀。');
      example.append(
        dateNode,
        text('：DIF '),
        difNode,
        text('、DEA '),
        deaNode,
        text('、柱 '),
        histNode,
        text('。'),
        stateNode
      );
      exampleItem.append(example);
      list.append(exampleItem);

      parts.body.append(list);
      return {
        details: parts.details,
        dateNode,
        difNode,
        deaNode,
        histNode,
        stateNode
      };
    }

    _makeRangeTable() {
      const parts = this._makeDetails('區間數據');
      parts.details.classList.add('kc-study-table-details');
      const hint = element(
        'p',
        'kc-study-table-hint',
        '表格只在展開時依目前可見日期更新，提供圖形以外的完整數值閱讀方式。'
      );
      const wrap = element('div', 'kc-study-table-wrap');
      wrap.tabIndex = 0;
      wrap.setAttribute('role', 'region');
      wrap.setAttribute('aria-label', '可捲動的日線副圖區間數據');

      const table = element('table', 'kc-study-table');
      const caption = element('caption', '', '目前可見區間的日線價量與MACD數據');
      const head = document.createElement('thead');
      const headRow = document.createElement('tr');
      const volumeHeader = this.volumeKind === 'amount' ? '成交金額（億元）' : '成交量（張／股）';
      const extra = [...(this.kinds.includes('kd') ? ['K', 'D'] : []), ...(this.kinds.includes('rsi') ? ['RSI'] : [])];
      ['日期', '開', '高', '低', '收', volumeHeader, 'DIF', 'DEA', '柱（DIF−DEA）', ...extra].forEach(value => {
        addCell(headRow, 'th', value, 'col');
      });
      head.append(headRow);
      const body = document.createElement('tbody');
      table.append(caption, head, body);
      wrap.append(table);
      parts.body.append(hint, wrap);
      return { details: parts.details, body };
    }

    _canvasSize(canvas, width, height) {
      const dpr = DPR();
      const pixelWidth = Math.max(1, Math.round(width * dpr));
      const pixelHeight = Math.max(1, Math.round(height * dpr));
      if (canvas.width !== pixelWidth) canvas.width = pixelWidth;
      if (canvas.height !== pixelHeight) canvas.height = pixelHeight;
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      const context = canvas.getContext('2d');
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
      context.clearRect(0, 0, width, height);
      return context;
    }

    _mainWidth() {
      if (this.chart.canvas && typeof this.chart.canvas.getBoundingClientRect === 'function') {
        const width = this.chart.canvas.getBoundingClientRect().width;
        if (finite(width) && width > 0) return Math.round(width);
      }
      const width = this.host.clientWidth;
      return finite(width) && width > 0 ? Math.round(width) : 600;
    }

    _range() {
      const candles = Array.isArray(this.chart.candles) ? this.chart.candles : [];
      const geo = this.chart.geo || {};
      const view = this.chart.view || {};
      if (!candles.length || !finite(geo.l) || !finite(geo.pw) || !finite(geo.step) || geo.step <= 0) {
        return { candles, valid: false, start: 0, end: 0, geo };
      }
      const start = Math.max(0, Math.min(candles.length, Math.round(geo.start)));
      const fallbackEnd = start + Math.max(0, Math.round(geo.pw / geo.step));
      const requestedEnd = finite(view.end) ? Math.round(view.end) : fallbackEnd;
      const end = Math.max(start, Math.min(candles.length, requestedEnd));
      return { candles, valid: end > start, start, end, geo };
    }

    _selectedIndex(range) {
      if (!range.valid) return -1;
      const hover = this.chart.hover;
      return Number.isInteger(hover) && hover >= range.start && hover < range.end
        ? hover
        : range.end - 1;
    }

    _drawEmpty(canvas, width, height, message) {
      const context = this._canvasSize(canvas, width, height);
      context.fillStyle = token('--dim');
      context.font = `12px ${token('--sans') || 'sans-serif'}`;
      context.textAlign = 'left';
      context.textBaseline = 'middle';
      context.fillText(message, 8, height / 2);
    }

    _drawCrosshair(context, height, range) {
      const hover = this.chart.hover;
      if (!Number.isInteger(hover) || hover < range.start || hover >= range.end) return;
      // 和 K 線那一段的十字線（kchart.js）同色、同一個水平位置，上下接起來才是一條線。
      const x = range.geo.l + range.geo.step * (hover - range.start + 0.5);
      context.save();
      context.strokeStyle = token('--faint');
      context.lineWidth = 1;
      context.setLineDash([3, 3]);
      context.beginPath();
      context.moveTo(x, 0);
      context.lineTo(x, height);
      context.stroke();
      context.restore();
    }

    // 選取那一天的數值標在右側 Y 軸的位置（本人 11-15：「選哪一天就顯示哪一天」）。原本的刻度不動，數值籤蓋到刻度時疊在上面。
    // values 是這一種指標那一天要標的數值 { name, value, text, bg }；y 把數值換成畫布上的高度。只畫目前顯示的那一種。
    _drawPicked(kind, context, width, height, range, y, values) {
      const hover = this.chart.hover;
      if (kind !== this.active || !Number.isInteger(hover) || hover < range.start || hover >= range.end) return;
      const tags = window.KChartTags, paper = token('--bg'), left = range.geo.l + range.geo.pw + 4;
      const items = values(hover).filter(item => finite(item.value)).map(item => ({ ...item, y: y(item.value), fg: paper }));
      tags.draw(context, left, width - left - 2, TAG_HEIGHT_PX, tags.place(items, TAG_HEIGHT_PX / 2 + 1, height - TAG_HEIGHT_PX / 2 - 1, TAG_HEIGHT_PX + 2), true);
      // 驗收用：這一天實際標出來的數值。
      this.debugPicked = items.map(({ name, text: value }) => ({ name, text: value }));
    }

    _drawVolume(context, width, height, range) {
      const { candles, start, end, geo } = range;
      const top = TITLE_STRIP_PX;
      const bottom = height - 14;
      const plotHeight = Math.max(1, bottom - top);
      const key = this.volumeKind;
      let maximum = 0;

      for (let index = start; index < end; index += 1) {
        const value = candles[index] && candles[index][key];
        if (finite(value) && value >= 0) maximum = Math.max(maximum, value);
      }
      const scale = maximum > 0 ? maximum : 1;
      const barWidth = Math.max(1, Math.min(14, geo.step * 0.64));

      context.save();
      context.beginPath();
      context.rect(geo.l, 0, geo.pw, height);
      context.clip();
      context.strokeStyle = token('--line');
      context.lineWidth = 1;
      context.beginPath();
      context.moveTo(geo.l, Math.round(bottom) + 0.5);
      context.lineTo(geo.l + geo.pw, Math.round(bottom) + 0.5);
      context.stroke();

      context.fillStyle = token('--dim');
      for (let index = start; index < end; index += 1) {
        const value = candles[index] && candles[index][key];
        if (!finite(value) || value < 0) continue;
        const x = geo.l + geo.step * (index - start + 0.5);
        const barHeight = value === 0 ? 0 : Math.max(1, value / scale * plotHeight);
        if (barHeight > 0) context.fillRect(x - barWidth / 2, bottom - barHeight, barWidth, barHeight);
      }
      context.restore();

      context.fillStyle = token('--dim');
      context.font = `11px ${token('--mono') || 'monospace'}`;
      context.textAlign = 'left';
      context.textBaseline = 'top';
      const axisX = Math.min(width - 2, geo.l + geo.pw + 7);
      const unitLabel = this.volumeKind === 'amount' ? '億元' : '張';
      context.fillText(unitLabel, axisX, top - 18);
      context.fillText(this.volumeKind === 'volume' ? formatNumber(maximum / 1000, 1) : formatNumber(maximum, 1), axisX, top - 5);
      context.textBaseline = 'bottom';
      context.fillText('0', axisX, height - 2);

      if (maximum === 0) {
        const hasFiniteZero = candles.slice(start, end).some(candle => candle && finite(candle[key]) && candle[key] === 0);
        if (!hasFiniteZero) {
          context.fillStyle = token('--dim');
          context.font = `12px ${token('--sans') || 'sans-serif'}`;
          context.textBaseline = 'middle';
          context.fillText(
            this.volumeKind === 'amount' ? '可見區間沒有成交金額資料' : '可見區間沒有成交量資料',
            geo.l + 6,
            height / 2
          );
        }
      }

      this._drawCrosshair(context, height, range);
      this._drawPicked('volume', context, width, height, range, value => bottom - value / scale * plotHeight, index => {
        const value = candles[index] && candles[index][key];
        return [{ name: '', value, text: formatNumber(this.volumeKind === 'volume' ? value / 1000 : value, 0), bg: token('--dim') }];
      });
    }

    _drawInstitutional(context, width, height, range, kind) {
      const top = TITLE_STRIP_PX;
      const bottom = height - 12;
      let amplitude = 0;
      for (let index = range.start; index < range.end; index += 1) {
        const value = range.candles[index] && range.candles[index][kind];
        if (finite(value)) amplitude = Math.max(amplitude, Math.abs(value));
      }
      amplitude = amplitude || 1;
      const middle = (top + bottom) / 2;
      const y = value => middle - value / amplitude * (bottom - top) / 2;
      const barWidth = Math.max(1, Math.min(14, range.geo.step * .64));
      context.save();
      context.beginPath(); context.rect(range.geo.l, 0, range.geo.pw, height); context.clip();
      context.strokeStyle = token('--line'); context.lineWidth = 1;
      context.beginPath(); context.moveTo(range.geo.l, Math.round(middle) + .5); context.lineTo(range.geo.l + range.geo.pw, Math.round(middle) + .5); context.stroke();
      for (let index = range.start; index < range.end; index += 1) {
        const value = range.candles[index] && range.candles[index][kind];
        if (!finite(value)) continue;
        const x = range.geo.l + range.geo.step * (index - range.start + .5);
        const valueY = y(value);
        context.fillStyle = value >= 0 ? token('--up') : token('--down');
        context.fillRect(x - barWidth / 2, Math.min(middle, valueY), barWidth, Math.max(1, Math.abs(valueY - middle)));
      }
      context.restore();
      context.fillStyle = token('--dim'); context.font = `11px ${token('--mono') || 'monospace'}`;
      context.textAlign = 'left'; context.textBaseline = 'middle';
      const axisX = Math.min(width - 2, range.geo.l + range.geo.pw + 7);
      context.fillText(institutionalText(amplitude, this.volumeKind, true), axisX, top + 3);
      context.fillText('0', axisX, middle);
      context.fillText(`−${institutionalText(amplitude, this.volumeKind, true)}`, axisX, bottom - 3);
      this._drawCrosshair(context, height, range);
      this._drawPicked(kind, context, width, height, range, y, index => {
        const value = range.candles[index] && range.candles[index][kind];
        const lots = formatNumber(Math.abs(value) / 1000, 0);
        return [{ name: '', value, text: value > 0 ? `+${lots}` : value < 0 ? `−${lots}` : lots, bg: value >= 0 ? token('--up') : token('--down') }];
      });
    }

    _drawMacdLine(context, range, y, field, color, dash) {
      context.save();
      context.strokeStyle = color;
      context.lineWidth = 1.4;
      context.setLineDash(dash);
      context.beginPath();
      let drawing = false;
      for (let index = range.start; index < range.end; index += 1) {
        const point = this.chart.macd && this.chart.macd[index];
        if (!point || point.ready !== true || !finite(point[field])) {
          drawing = false;
          continue;
        }
        const x = range.geo.l + range.geo.step * (index - range.start + 0.5);
        if (drawing) context.lineTo(x, y(point[field]));
        else context.moveTo(x, y(point[field]));
        drawing = true;
      }
      context.stroke();
      context.restore();
    }

    _drawMacd(context, width, height, range) {
      const macd = Array.isArray(this.chart.macd) ? this.chart.macd : [];
      const top = TITLE_STRIP_PX;
      const bottom = height - 12;
      let amplitude = 0;
      let hasReady = false;

      for (let index = range.start; index < range.end; index += 1) {
        const point = macd[index];
        if (!point || point.ready !== true) continue;
        ['dif', 'dea', 'hist'].forEach(field => {
          if (finite(point[field])) {
            hasReady = true;
            amplitude = Math.max(amplitude, Math.abs(point[field]));
          }
        });
      }
      amplitude = amplitude > 0 ? amplitude * 1.1 : 1;
      const middle = top + (bottom - top) / 2;
      const halfHeight = Math.max(1, (bottom - top) / 2);
      const y = value => middle - value / amplitude * halfHeight;
      const zeroY = y(0);
      const barWidth = Math.max(1, Math.min(14, range.geo.step * 0.64));

      context.save();
      context.beginPath();
      context.rect(range.geo.l, 0, range.geo.pw, height);
      context.clip();

      context.strokeStyle = token('--line');
      context.lineWidth = 1;
      context.beginPath();
      context.moveTo(range.geo.l, Math.round(zeroY) + 0.5);
      context.lineTo(range.geo.l + range.geo.pw, Math.round(zeroY) + 0.5);
      context.stroke();

      for (let index = range.start; index < range.end; index += 1) {
        const point = macd[index];
        if (!point || point.ready !== true || !finite(point.hist)) continue;
        const x = range.geo.l + range.geo.step * (index - range.start + 0.5);
        const valueY = y(point.hist);
        context.fillStyle = point.hist >= 0 ? token('--up') : token('--down');
        context.globalAlpha = 0.78;
        context.fillRect(
          x - barWidth / 2,
          Math.min(zeroY, valueY),
          barWidth,
          Math.max(1, Math.abs(valueY - zeroY))
        );
      }
      context.globalAlpha = 1;
      this._drawMacdLine(context, range, y, 'dif', token('--study-dif'), []);
      this._drawMacdLine(context, range, y, 'dea', token('--study-dea'), [5, 3]);
      context.restore();

      context.fillStyle = token('--dim');
      context.font = `11px ${token('--mono') || 'monospace'}`;
      context.textAlign = 'left';
      context.textBaseline = 'middle';
      const axisX = Math.min(width - 2, range.geo.l + range.geo.pw + 7);
      context.fillText(formatNumber(amplitude / 1.1, 1), axisX, top + 2);
      context.fillText('0 零線', axisX, zeroY);
      context.fillText(`−${formatNumber(amplitude / 1.1, 1)}`, axisX, bottom - 2);

      if (!hasReady) {
        context.fillStyle = token('--dim');
        context.font = `12px ${token('--sans') || 'sans-serif'}`;
        context.textBaseline = 'middle';
        context.fillText(this.chart.macdNotice || '暖機中：需34根有效日線', range.geo.l + 6, height / 2);
      }

      this._drawCrosshair(context, height, range);
      this._drawPicked('macd', context, width, height, range, y, index => {
        const point = macd[index];
        if (!point || point.ready !== true) return [];
        return [
          { name: 'DIF', value: point.dif, text: formatNumber(point.dif, 1), bg: token('--study-dif') },
          { name: 'DEA', value: point.dea, text: formatNumber(point.dea, 1), bg: token('--study-dea') },
          { name: '柱', value: point.hist, text: `${point.hist < 0 ? '−' : '+'}${formatNumber(Math.abs(point.hist), 1)}`, bg: point.hist >= 0 ? token('--up') : token('--down') }
        ];
      });
    }

    // KD／RSI 共用：固定 0～100 縱軸，畫參考虛線與一到兩條指標線；暖機中的點不畫成 0。
    _drawOscillator(kind, context, width, height, range, series, lines, refs, emptyText) {
      const top = TITLE_STRIP_PX;
      const bottom = height - 12;
      const y = value => bottom - value / 100 * (bottom - top);
      let hasReady = false;
      context.save();
      context.beginPath();
      context.rect(range.geo.l, 0, range.geo.pw, height);
      context.clip();
      context.strokeStyle = token('--line');
      context.lineWidth = 1;
      context.setLineDash([4, 4]);
      for (const ref of refs) {
        context.beginPath();
        context.moveTo(range.geo.l, Math.round(y(ref)) + 0.5);
        context.lineTo(range.geo.l + range.geo.pw, Math.round(y(ref)) + 0.5);
        context.stroke();
      }
      for (const [field, color, dash] of lines) {
        context.strokeStyle = color;
        context.lineWidth = 1.4;
        context.setLineDash(dash);
        context.beginPath();
        let drawing = false;
        for (let index = range.start; index < range.end; index += 1) {
          const point = series[index];
          if (!point || point.ready !== true || !finite(point[field])) { drawing = false; continue; }
          hasReady = true;
          const x = range.geo.l + range.geo.step * (index - range.start + 0.5);
          if (drawing) context.lineTo(x, y(point[field])); else context.moveTo(x, y(point[field]));
          drawing = true;
        }
        context.stroke();
      }
      context.restore();

      context.fillStyle = token('--dim');
      context.font = `11px ${token('--mono') || 'monospace'}`;
      context.textAlign = 'left';
      context.textBaseline = 'middle';
      const axisX = Math.min(width - 2, range.geo.l + range.geo.pw + 7);
      for (const ref of refs) context.fillText(String(ref), axisX, y(ref));
      if (!hasReady) {
        context.font = `12px ${token('--sans') || 'sans-serif'}`;
        context.fillText(emptyText, range.geo.l + 6, height / 2);
      }
      this._drawCrosshair(context, height, range);
      this._drawPicked(kind, context, width, height, range, y, index => {
        const point = series[index];
        if (!point || point.ready !== true) return [];
        // 只有一條線的指標（RSI）不寫短名，指標名稱已經在左上角
        return lines.map(([field, color]) => ({ name: lines.length > 1 ? field.toUpperCase() : '', value: point[field], text: formatNumber(point[field], 1), bg: color }));
      });
    }

    _ensureOscillators(candles) {
      if (this._oscRef === candles) return;
      this._oscRef = candles;
      this.kd = this.kdPane ? kdSeries(candles) : [];
      this.rsi = this.rsiPane ? rsiSeries(candles) : [];
    }

    _updateOscillatorReadouts(candle, date, selectedIndex) {
      if (this.kdPane) {
        const point = selectedIndex >= 0 ? this.kd[selectedIndex] : null;
        const ready = point && point.ready && finite(point.k) && finite(point.d);
        this.kdReadout.textContent = !candle ? '尚無日線資料' : !ready ? `${date}｜KD 暖機中，暫無數值`
          : `${date}｜K ${formatNumber(point.k, 2)}｜D ${formatNumber(point.d, 2)}`;
        this.kdCanvas.setAttribute('aria-label', candle && ready
          ? `KD副圖；日期：${date}；K：${formatNumber(point.k, 2)}；D：${formatNumber(point.d, 2)}。`
          : 'KD副圖；暖機或資料不足。');
      }
      if (this.rsiPane) {
        const point = selectedIndex >= 0 ? this.rsi[selectedIndex] : null;
        const ready = point && point.ready && finite(point.rsi);
        this.rsiReadout.textContent = !candle ? '尚無日線資料' : !ready ? `${date}｜RSI 暖機中或近 14 日無漲跌，暫無數值`
          : `${date}｜RSI ${formatNumber(point.rsi, 1)}`;
        this.rsiCanvas.setAttribute('aria-label', candle && ready
          ? `RSI副圖；日期：${date}；RSI：${formatNumber(point.rsi, 1)}。`
          : 'RSI副圖；暖機或資料不足。');
      }
    }

    _updateReadouts(range, selectedIndex) {
      const candle = selectedIndex >= 0 ? range.candles[selectedIndex] : null;
      const date = labelFor(candle);
      if (this.kdPane || this.rsiPane) this._updateOscillatorReadouts(candle, date, selectedIndex);
      const value = candle && candle[this.volumeKind];
      const point = selectedIndex >= 0 && Array.isArray(this.chart.macd) ? this.chart.macd[selectedIndex] : null;
      for (const [kind, parts] of Object.entries(this.institutionalPanes || {})) {
        const net = candle && candle[kind];
        const label = { foreign: '外資', trust: '投信', dealer: '自營商' }[kind];
        parts.readout.textContent = candle && finite(net)
          ? `${date}｜${label} ${net > 0 ? '+' : net < 0 ? '−' : ''}${institutionalText(net, this.volumeKind)}`
          : `${date}｜資料不足`;
        parts.canvas.setAttribute('aria-label', candle && finite(net)
          ? `${label}買賣超；日期：${date}；${net >= 0 ? '買超' : '賣超'} ${institutionalText(net, this.volumeKind)}。`
          : `${label}買賣超；日期：${date}；資料不足。`);
      }
      if (!candle) {
        this.volumeReadout.textContent = '尚無日線資料';
        this.macdReadout.textContent = '尚無日線資料';
        this.volumeCanvas.setAttribute('aria-label', this.volumeKind === 'amount' ? '成交金額副圖；日期尚無；單位：億元。' : '成交量副圖；日期尚無；單位：張與股。');
        this.macdCanvas.setAttribute('aria-label', 'MACD副圖；日期尚無；單位同主圖價格。');
        return;
      }
      if (this.volumeKind === 'amount') {
        const valueText = finite(value) ? `${formatNumber(value, 2)} 億元` : '資料不足';
        this.volumeReadout.textContent = `${date}｜成交金額 ${valueText}`;
        this.volumeCanvas.setAttribute('aria-label', `成交金額副圖；日期：${date}；成交金額：${valueText}；單位：億元。`);
      } else {
        const valueText = finite(value) ? `${formatNumber(value / 1000, 3)} 張（${formatNumber(value, 0)} 股）` : '資料不足';
        this.volumeReadout.textContent = `${date}｜成交量 ${valueText}`;
        this.volumeCanvas.setAttribute('aria-label', `成交量副圖；日期：${date}；成交量：${valueText}；單位：張與股。`);
      }
      const macdReady = point && point.ready === true && finite(point.dif) && finite(point.dea) && finite(point.hist);
      if (macdReady) {
        const difText = formatMacd(point.dif), deaText = formatMacd(point.dea), histText = formatSigned(point.hist);
        this.macdReadout.textContent = `${date}｜DIF ${difText}｜DEA ${deaText}｜柱 ${histText}`;
        this.macdCanvas.setAttribute('aria-label', `MACD副圖；日期：${date}；DIF：${difText}；DEA：${deaText}；柱：${histText}；單位同主圖價格。`);
      } else {
        this.macdReadout.textContent = `${date}｜${this.chart.macdNotice || 'MACD 暖機或資料不足，暫無完整數值'}`;
        this.macdCanvas.setAttribute('aria-label', `MACD副圖；日期：${date}；暖機或資料不足；單位同主圖價格。`);
      }
    }

    _tableRangeKey(range) {
      if (this._candlesRef !== range.candles) {
        this._candlesRef = range.candles;
        this._candlesVersion += 1;
      }
      const lastIndex = range.end - 1;
      const last = lastIndex >= range.start ? range.candles[lastIndex] : null;
      const lastMacd = lastIndex >= range.start && Array.isArray(this.chart.macd)
        ? this.chart.macd[lastIndex]
        : null;
      const volume = last && last[this.volumeKind];
      return [
        this._candlesVersion,
        range.start,
        range.end,
        last && last.t,
        last && last.open,
        last && last.high,
        last && last.low,
        last && last.close,
        volume,
        lastMacd && lastMacd.ready,
        lastMacd && lastMacd.dif,
        lastMacd && lastMacd.dea,
        lastMacd && lastMacd.hist
      ].join('|');
    }

    _updateTable() {
      if (!this.tableDetails?.open || !this.tableBody || this.chart.unit !== '日') return;
      const range = this._range();
      const key = this._tableRangeKey(range);
      if (key === this._tableKey) return;
      this._tableKey = key;

      const fragment = document.createDocumentFragment();
      if (!range.valid) {
        const row = document.createElement('tr');
        const cell = addCell(row, 'td', '目前沒有可顯示的日線區間資料。');
        cell.colSpan = 9 + (this.kdPane ? 2 : 0) + (this.rsiPane ? 1 : 0);
        fragment.append(row);
        this.tableBody.replaceChildren(fragment);
        return;
      }

      const macd = Array.isArray(this.chart.macd) ? this.chart.macd : [];
      for (let index = range.start; index < range.end; index += 1) {
        const candle = range.candles[index] || {};
        const point = macd[index];
        const ready = point && point.ready === true;
        const volumeValue = candle[this.volumeKind];
        const volumeText = this.volumeKind === 'amount'
          ? (finite(volumeValue) ? formatNumber(volumeValue, 2) : '缺值')
          : (finite(volumeValue)
              ? `${formatNumber(volumeValue / 1000, 3)} 張／${formatNumber(volumeValue, 0)} 股`
              : '缺值');
        const row = document.createElement('tr');
        addCell(row, 'th', labelFor(candle), 'row');
        addCell(row, 'td', formatPrice(candle.open));
        addCell(row, 'td', formatPrice(candle.high));
        addCell(row, 'td', formatPrice(candle.low));
        addCell(row, 'td', formatPrice(candle.close));
        addCell(row, 'td', volumeText);
        addCell(row, 'td', ready && finite(point.dif) ? formatMacd(point.dif) : '缺值');
        addCell(row, 'td', ready && finite(point.dea) ? formatMacd(point.dea) : '缺值');
        addCell(row, 'td', ready && finite(point.hist) ? formatSigned(point.hist) : '缺值');
        if (this.kdPane) {
          const kd = this.kd[index];
          const kdReady = kd && kd.ready && finite(kd.k) && finite(kd.d);
          addCell(row, 'td', kdReady ? formatNumber(kd.k, 2) : '暖機');
          addCell(row, 'td', kdReady ? formatNumber(kd.d, 2) : '暖機');
        }
        if (this.rsiPane) {
          const rsi = this.rsi[index];
          addCell(row, 'td', rsi && rsi.ready && finite(rsi.rsi) ? formatNumber(rsi.rsi, 1) : '缺值');
        }
        fragment.append(row);
      }
      this.tableBody.replaceChildren(fragment);
    }

    draw() {
      const daily = this.chart.unit === '日';
      this.debugPicked = [];
      this.notice.hidden = daily;
      // 非日線沒有指標區，指標的選單也跟著收起來（不然會懸在空的地方）
      if (!daily && !this.switcher.hidden) {
        this.switcher.hidden = true;
        for (const chip of this.root.querySelectorAll('.chart-chip')) chip.setAttribute('aria-expanded', 'false');
      }
      for (const button of this.switcher.querySelectorAll('[data-study]')) {
        button.setAttribute('aria-pressed', String(button.dataset.study === this.active));
      }
      this.volumePane.hidden = !daily || this.active !== 'volume';
      this.macdPane.hidden = !daily || this.active !== 'macd';
      if (this.kdPane) this.kdPane.hidden = !daily || this.active !== 'kd';
      if (this.rsiPane) this.rsiPane.hidden = !daily || this.active !== 'rsi';
      for (const [kind, parts] of Object.entries(this.institutionalPanes || {})) parts.pane.hidden = !daily || this.active !== kind;
      if (!daily) return;

      const width = this._mainWidth();
      const height = width >= 760 ? 132 : 116;
      const range = this._range();
      this._ensureOscillators(range.candles);
      const selectedIndex = this._selectedIndex(range);
      this._updateReadouts(range, selectedIndex);

      if (!range.valid) {
        this._drawEmpty(this.volumeCanvas, width, height, '尚無可顯示的日線量能');
        this._drawEmpty(this.macdCanvas, width, height, '尚無可顯示的日線MACD');
        if (this.kdPane) this._drawEmpty(this.kdCanvas, width, height, '尚無可顯示的日線KD');
        if (this.rsiPane) this._drawEmpty(this.rsiCanvas, width, height, '尚無可顯示的日線RSI');
        for (const parts of Object.values(this.institutionalPanes || {})) this._drawEmpty(parts.canvas, width, height, '尚無法人資料');
      } else {
        const volumeContext = this._canvasSize(this.volumeCanvas, width, height);
        this._drawVolume(volumeContext, width, height, range);
        const macdContext = this._canvasSize(this.macdCanvas, width, height);
        this._drawMacd(macdContext, width, height, range);
        if (this.kdPane) {
          this._drawOscillator('kd', this._canvasSize(this.kdCanvas, width, height), width, height, range, this.kd,
            [['k', token('--study-dif'), []], ['d', token('--study-dea'), [5, 3]]], [80, 20], '暖機中：需49根有效日線');
        }
        if (this.rsiPane) {
          this._drawOscillator('rsi', this._canvasSize(this.rsiCanvas, width, height), width, height, range, this.rsi,
            [['rsi', token('--study-dif'), []]], [70, 50, 30], '暖機中：需15根有效日線');
        }
        for (const [kind, parts] of Object.entries(this.institutionalPanes || {})) {
          this._drawInstitutional(this._canvasSize(parts.canvas, width, height), width, height, range, kind);
        }
      }
    }
  }

  // 大盤日 K 接上官方三大法人淨額（元 → 億元）。只接同一天的；官方沒有的日子或欄位留空，不拿別天補。
  ChartStudies.withMarketInstitutional = function withMarketInstitutional(candles, rows) {
    const byDate = new Map((rows || []).map(row => [row.date, row]));
    return candles.map(candle => {
      const row = byDate.get(candle.t);
      if (!row) return candle;
      const out = { ...candle };
      for (const kind of ['foreign', 'trust', 'dealer']) {
        const net = row[kind] && row[kind].net;
        if (finite(net)) out[kind] = net / 100_000_000;
      }
      return out;
    });
  };

  window.ChartStudies = ChartStudies;
})();
