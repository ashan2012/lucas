/* 拼音 + 笔顺拼图 —— 多关卡 / 干扰笔画 / 自动演示 */
(() => {
  "use strict";

  const NS = "http://www.w3.org/2000/svg";
  const DATA = window.GAME_DATA;

  // The 田字格 is drawn on a 200x200 canvas and scaled by CSS.
  const VIEW = 200;
  const GLYPH = { cx: 520, cy: 500, flip: 900, extent: 900, fill: 0.86 };

  const TONE_GLYPH = { 1: "ˉ", 2: "ˊ", 3: "ˇ", 4: "ˋ" };
  const ACCENT = {
    a: ["a", "ā", "á", "ǎ", "à"],
    e: ["e", "ē", "é", "ě", "è"],
    i: ["i", "ī", "í", "ǐ", "ì"],
    o: ["o", "ō", "ó", "ǒ", "ò"],
    u: ["u", "ū", "ú", "ǔ", "ù"],
    "ü": ["ü", "ǖ", "ǘ", "ǚ", "ǜ"],
  };

  const DECOYS = DATA.decoys || [];

  const state = {
    sound: true,
    index: 0,
    mode: "main", // main = 顺序闯关，review = 错题重练
    trackPos: 0,
    reviewTrack: [],
    review: [], // 拖错过的关卡（按页码排序）
    mainPos: 0, // 进复习前停在主序列的哪一关
    cardName: "",
    cardSpent: 0, // 本张卡片已经用掉的时间（不含当前关）
    score: 0, // 累计
    levelScore: 0,
    levelMax: 0,
    mistakes: 0,
    startedAt: 0,
    finalMs: 0,
    timerId: null,
    finished: false,
    demo: false,
    active: 0,
    chars: [],
  };

  // 主序列：整本材料的全部关卡
  const ALL_LEVELS = DATA.levels.map((_, i) => i);

  const $ = (sel, root) => (root || document).querySelector(sel);

  // ------------------------------------------------------------------ utils
  function svgEl(tag, attrs) {
    const node = document.createElementNS(NS, tag);
    Object.entries(attrs || {}).forEach(([k, v]) => node.setAttribute(k, v));
    return node;
  }

  function cellMatrix() {
    const s = (VIEW * GLYPH.fill) / GLYPH.extent;
    const tx = VIEW / 2 - GLYPH.cx * s;
    const ty = VIEW / 2 + (GLYPH.flip - GLYPH.cy) * s;
    return `matrix(${s} 0 0 ${-s} ${tx} ${ty})`;
  }

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  /* Fit a single stroke into a square chip: flip the design canvas, then take
     the path's own bounding box as viewBox. */
  const scratch = svgEl("svg", {
    width: 10,
    height: 10,
    style: "position:absolute;left:-9999px;top:0;width:10px;height:10px;overflow:hidden",
  });

  function chipStrokeSvg(d) {
    const svg = svgEl("svg", { viewBox: "0 0 100 100", preserveAspectRatio: "xMidYMid meet" });
    const group = svgEl("g", { transform: "translate(0 900) scale(1 -1)" });
    const path = svgEl("path", { d });
    group.appendChild(path);
    svg.appendChild(group);

    scratch.appendChild(svg);
    const box = path.getBBox();
    scratch.removeChild(svg);

    const pad = Math.max(box.width, box.height) * 0.14 + 4;
    const x = box.x - pad;
    const y = 900 - box.y - box.height - pad;
    svg.setAttribute(
      "viewBox",
      `${x.toFixed(2)} ${y.toFixed(2)} ${(box.width + pad * 2).toFixed(2)} ${(box.height + pad * 2).toFixed(2)}`
    );
    return svg;
  }

  function toast(message, kind) {
    const node = $("#toast");
    node.textContent = message;
    node.className = "toast show" + (kind ? " " + kind : "");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => node.classList.remove("show"), 1900);
  }

  function floatScore(x, y, text) {
    const node = document.createElement("div");
    node.className = "float-score";
    node.textContent = text;
    node.style.left = x + "px";
    node.style.top = y + "px";
    document.body.appendChild(node);
    setTimeout(() => node.remove(), 950);
  }

  // ------------------------------------------------------------------ sound
  let audio = null;
  function beep(freq, duration, type, volume) {
    if (!state.sound) return;
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      const osc = audio.createOscillator();
      const gain = audio.createGain();
      osc.type = type || "sine";
      osc.frequency.value = freq;
      const now = audio.currentTime;
      gain.gain.setValueAtTime(volume || 0.05, now);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + duration);
      osc.connect(gain).connect(audio.destination);
      osc.start(now);
      osc.stop(now + duration);
    } catch (err) {
      /* audio is a bonus, never break the game */
    }
  }

  const soundGood = () => {
    beep(880, 0.08, "sine", 0.05);
    setTimeout(() => beep(1320, 0.12, "sine", 0.04), 70);
  };
  const soundBad = () => beep(170, 0.16, "triangle", 0.05);

  // ------------------------------------------------------------------- clock
  function startClock() {
    if (state.startedAt || state.finished || state.demo) return;
    state.startedAt = Date.now();
    state.timerId = setInterval(() => {
      $("#timer").textContent = fmt(elapsed());
      $("#card-timer").textContent = "本卡用时 " + fmt(cardElapsed());
    }, 250);
  }

  function elapsed() {
    if (state.finished) return state.finalMs;
    return state.startedAt ? Date.now() - state.startedAt : 0;
  }

  // 整卡计时：前面关卡累计的时间 + 当前这一关已用时间
  function cardElapsed() {
    return state.cardSpent + elapsed();
  }

  function fmt(ms) {
    const total = Math.floor(ms / 1000);
    return String(Math.floor(total / 60)).padStart(2, "0") + ":" + String(total % 60).padStart(2, "0");
  }

  // -------------------------------------------------------------------- drag
  let drag = null;
  let hover = null;

  function targetAt(x, y) {
    const node = document.elementFromPoint(x, y);
    if (!node || !node.closest) return null;
    return node.closest(".letter-slot, .tone-slot, .cell-wrap");
  }

  function setHover(node) {
    if (hover === node) return;
    if (hover) hover.classList.remove("hover");
    hover = node;
    if (hover) hover.classList.add("hover");
  }

  function moveGhost(x, y) {
    if (!drag) return;
    drag.ghost.style.transform = `translate(${x - drag.ox}px, ${y - drag.oy}px)`;
  }

  function makeDraggable(node, payload) {
    node.addEventListener("pointerdown", (ev) => {
      if (state.finished || state.demo || drag) return;
      if (ev.button) return;
      ev.preventDefault();
      startClock();

      const rect = node.getBoundingClientRect();
      const ghost = node.cloneNode(true);
      ghost.classList.add("ghost");
      ghost.classList.remove("dragging", "pulse");
      ghost.style.width = rect.width + "px";
      ghost.style.height = rect.height + "px";
      ghost.style.fontSize = getComputedStyle(node).fontSize;
      document.body.appendChild(ghost);

      node.classList.add("dragging");
      drag = {
        node,
        ghost,
        payload: Object.assign({ node }, payload),
        ox: ev.clientX - rect.left,
        oy: ev.clientY - rect.top,
      };
      moveGhost(ev.clientX, ev.clientY);
      setHover(targetAt(ev.clientX, ev.clientY));

      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
      window.addEventListener("pointercancel", onCancel);
    });
  }

  function onMove(ev) {
    if (!drag) return;
    moveGhost(ev.clientX, ev.clientY);
    setHover(targetAt(ev.clientX, ev.clientY));
  }

  function onUp(ev) {
    if (!drag) return;
    const target = targetAt(ev.clientX, ev.clientY);
    const payload = drag.payload;
    const x = ev.clientX;
    const y = ev.clientY;
    onCancel();
    if (target) attemptDrop(payload, target, x, y);
  }

  function onCancel() {
    if (!drag) return;
    drag.ghost.remove();
    drag.node.classList.remove("dragging");
    setHover(null);
    drag = null;
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerup", onUp);
    window.removeEventListener("pointercancel", onCancel);
  }

  // ------------------------------------------------------------------- drops
  function award(x, y) {
    state.levelScore += 2;
    state.score += 2;
    $("#score").textContent = state.levelScore;
    const stat = $("#score").closest(".stat");
    stat.classList.remove("bump");
    void stat.offsetWidth;
    stat.classList.add("bump");
    floatScore(x, y, "+2");
    soundGood();
  }

  function reject(target, message) {
    state.mistakes += 1;
    const node = target || document.body;
    node.classList.add("shake", "wrong-flash");
    setTimeout(() => node.classList.remove("shake", "wrong-flash"), 430);
    toast(message, "bad");
    soundBad();
  }

  function attemptDrop(payload, target, x, y) {
    if (payload.kind === "letter") dropLetter(payload, target, x, y);
    else if (payload.kind === "tone") dropTone(payload, target, x, y);
    else if (payload.kind === "stroke") dropStroke(payload, target, x, y);
  }

  function dropLetter(payload, target, x, y) {
    const slot = target.matches(".letter-slot") ? target : target.closest(".letter-slot");
    if (!slot) return reject(target, "字母要放进拼音方框里");
    const ci = Number(slot.dataset.char);
    const li = Number(slot.dataset.letter);
    const st = state.chars[ci];

    if (ci !== state.active) return reject(slot, "先拼好前面那个字");
    if (st.letters[li]) return reject(slot, "这个方框已经拼好啦");
    if (st.syllable.letters[li] !== payload.value) return reject(slot, "字母不对，再看一眼");

    applyLetter(ci, li);
    award(x, y);
    checkDone(ci);
  }

  function dropTone(payload, target, x, y) {
    const slot = target.matches(".tone-slot") ? target : target.closest(".tone-slot");
    if (!slot) return reject(target, "声调要放到字母上方的圆圈里");
    const ci = Number(slot.dataset.char);
    const st = state.chars[ci];

    if (ci !== state.active) return reject(slot, "先拼好前面那个字");
    if (st.tonePlaced) return reject(slot, "声调已经放好喽");
    if (!st.letters[st.syllable.toneAt]) return reject(slot, "先把这个字母放上去");
    if (Number(payload.value) !== Number(st.syllable.tone)) return reject(slot, "声调不对，再听听");

    applyTonePlace(ci);
    award(x, y);
    checkDone(ci);
  }

  function dropStroke(payload, target, x, y) {
    const cell = target.matches(".cell-wrap") ? target : target.closest(".cell-wrap");
    if (!cell) return reject(target, "笔画要拖进田字格里");
    const ci = Number(cell.dataset.char);
    const st = state.chars[ci];

    if (payload.decoy) return reject(cell, `这笔不是「${st.char}」的，找找别的`);
    if (ci !== state.active) return reject(cell, "先写前面那个字");
    if (payload.index !== st.nextStroke) {
      return reject(cell, `笔顺不对：现在该写第 ${st.nextStroke + 1} 笔`);
    }

    applyStroke(ci, payload.index);
    payload.node.remove();
    award(x, y);
    checkDone(ci);
  }

  // --- state mutations shared by drops and the demo ------------------------
  function applyLetter(ci, li) {
    const st = state.chars[ci];
    const slot = document.querySelector(`.letter-slot[data-char="${ci}"][data-letter="${li}"]`);
    if (!slot) return;
    st.letters[li] = true;
    slot.textContent = st.syllable.letters[li];
    slot.classList.add("filled");
  }

  function applyTonePlace(ci) {
    const st = state.chars[ci];
    const slot = document.querySelector(`.tone-slot[data-char="${ci}"]`);
    if (!slot) return;
    st.tonePlaced = true;
    slot.textContent = TONE_GLYPH[st.syllable.tone];
    slot.classList.add("filled");

    const letterSlot = document.querySelector(
      `.letter-slot[data-char="${ci}"][data-letter="${st.syllable.toneAt}"]`
    );
    const row = ACCENT[st.syllable.letters[st.syllable.toneAt]];
    if (letterSlot && row) letterSlot.textContent = row[st.syllable.tone];
  }

  function applyStroke(ci, index) {
    const st = state.chars[ci];
    const ink = document.querySelector(`.cell-wrap[data-char="${ci}"] .ink`);
    if (!ink) return;
    ink.appendChild(svgEl("path", { d: DATA.strokes[st.char][index], class: "pop" }));
    st.nextStroke += 1;
    updateMeta(ci);
  }

  function updateMeta(ci) {
    const st = state.chars[ci];
    const meta = document.querySelector(`.char-col[data-char="${ci}"] .cell-meta b`);
    if (meta) meta.textContent = st.nextStroke;
    updateStrokeNote();
  }

  function updateStrokeNote() {
    const st = state.chars[state.active];
    if (!st) return;
    $("#stroke-note").textContent = `第 ${state.active + 1} 个字「${st.char}」· 还剩 ${
      st.strokeCount - st.nextStroke
    } 笔`;
  }

  function checkDone(ci) {
    const st = state.chars[ci];
    const lettersDone = st.letters.every(Boolean);
    const toneDone = !st.syllable.tone || st.tonePlaced;
    const strokesDone = st.nextStroke >= st.strokeCount;
    if (!(lettersDone && toneDone && strokesDone)) return;

    st.done = true;
    const col = document.querySelector(`.char-col[data-char="${ci}"]`);
    if (col) {
      col.classList.remove("active");
      col.classList.add("done");
    }

    if (ci + 1 < state.chars.length) {
      state.active = ci + 1;
      syncActive();
      if (!state.demo) {
        toast(`「${st.char}」拼好啦，做下一个字`, "good");
        soundGood();
      }
    } else {
      completeLevel();
    }
  }

  // ------------------------------------------------------------------ levels
  function level() {
    return DATA.levels[state.index];
  }

  function track() {
    return state.mode === "review" ? state.reviewTrack : ALL_LEVELS;
  }

  // 主序列跳关：下拉框、从头再来都走这里
  function goLevel(index) {
    state.mode = "main";
    state.trackPos = Math.max(0, Math.min(ALL_LEVELS.length - 1, index));
    startLevel(state.trackPos);
  }

  function startLevel(index) {
    const next = Math.max(0, Math.min(DATA.levels.length - 1, index));
    // 整卡计时：同一张卡片累加，换卡重新计
    const leaving = state.chars.length ? elapsed() : 0;
    state.cardSpent = state.cardName && state.cardName === DATA.levels[next].card
      ? state.cardSpent + leaving
      : 0;
    state.cardName = DATA.levels[next].card;

    state.index = next;
    if (state.mode === "main") state.trackPos = next;
    if (state.mode === "main" && next === 0) state.score = 0; // 从头开始，累计分归零
    state.finished = false;
    state.demo = false;
    state.levelScore = 0;
    state.mistakes = 0;
    state.startedAt = 0;
    state.finalMs = 0;
    state.active = 0;
    clearInterval(state.timerId);
    state.timerId = null;

    const lv = level();
    state.chars = lv.chars.map((spec) => ({
      char: spec.char,
      syllable: spec.syllable,
      strokeCount: (DATA.strokes[spec.char] || []).length,
      letters: new Array(spec.syllable.letters.length).fill(false),
      tonePlaced: false,
      nextStroke: 0,
      done: false,
    }));
    state.levelMax =
      state.chars.reduce(
        (sum, st) => sum + st.letters.length + (st.syllable.tone ? 1 : 0) + st.strokeCount,
        0
      ) * 2;

    $("#overlay").hidden = true;
    $("#timer").textContent = "00:00";
    $("#card-timer").textContent = "本卡用时 " + fmt(state.cardSpent);
    $("#score").textContent = "0";
    $("#score-total").textContent = "/ " + state.levelMax;
    $("#level-index").textContent = String(state.trackPos + 1);
    $("#level-total").textContent = "/ " + track().length;
    $("#level-label").textContent = state.mode === "review" ? "复习" : "关卡";
    document.body.classList.toggle("review-mode", state.mode === "review");
    $("#word-title").textContent = `${lv.hanzi} · 拼音笔顺拼图`;
    const sameCard = DATA.levels.filter((item) => item.card === lv.card);
    const within = sameCard.indexOf(lv) + 1;
    $("#card-name").textContent =
      `${lv.card}${lv.lesson ? " · " + lv.lesson : ""} · 本卡第 ${within}/${sameCard.length} 关`;
    $("#level-select").value = String(state.index);
    document.title = `${lv.hanzi} · 拼音笔顺拼图`;

    buildWord();
    syncActive();
    updateReviewBtn();
    startClock(); // 一进关就开始计时
  }

  function completeLevel() {
    if (state.demo) return;
    // 先把时间取出来：finished 一置真，elapsed() 就只会返回 finalMs
    const spent = elapsed();
    state.finished = true;
    state.finalMs = spent;
    clearInterval(state.timerId);
    $("#timer").textContent = fmt(state.finalMs);
    $("#card-timer").textContent = "本卡用时 " + fmt(cardElapsed());
    // 写完了就把口袋里剩下的干扰笔画收走
    $("#stroke-pool").innerHTML = "";
    $("#stroke-note").textContent = "全部写完啦，点「下一关」";

    // 错题本：拖错过就收进去，一次没错就放出来
    if (state.mistakes === 0) dropFromReview(state.index);
    else addToReview(state.index);

    const list = track();
    const last = state.trackPos + 1 >= list.length;
    const nextIndex = last ? null : list[state.trackPos + 1];
    const cardDone = nextIndex !== null && DATA.levels[nextIndex].card !== level().card;

    if (state.mode === "review") {
      $("#overlay-title").textContent = last ? "错题复习完成！" : "这道错题复习好啦！";
    } else if (last) {
      $("#overlay-title").textContent = "太厉害了，全部通关！";
    } else if (cardDone) {
      $("#overlay-title").textContent = `${level().card} 这一张全部拼完啦！`;
    } else {
      $("#overlay-title").textContent = "这一关拼好啦！";
    }

    $("#overlay-word").textContent = `${level().hanzi}　${level().pinyin}`;
    $("#final-score").textContent = String(state.levelScore);
    $("#final-time").textContent = fmt(state.finalMs);
    $("#final-card").textContent = fmt(cardElapsed());
    const perfect = state.levelScore / 2;
    $("#final-acc").textContent = Math.round((perfect / Math.max(1, perfect + state.mistakes)) * 100) + "%";
    $("#total-score").textContent = String(state.score);
    $("#next-level").textContent = state.mode === "review"
      ? (last ? "回到闯关" : "下一题")
      : (last ? "从头再来" : "下一关");
    $("#overlay").hidden = false;
    soundGood();
    setTimeout(soundGood, 260);
  }

  function nextLevel() {
    const list = track();
    if (state.trackPos + 1 < list.length) {
      state.trackPos += 1;
      startLevel(list[state.trackPos]);
      return;
    }
    if (state.mode === "review") {
      exitReview();
      return;
    }
    goLevel(0);
  }

  // ------------------------------------------------------------------ review
  /* 错题本跨天保留：存「卡片|词语」而不是序号，重新生成页面（关卡重新排）也不会错位 */
  const REVIEW_STORE = "zige-review-v1";

  function reviewKey(index) {
    const lv = DATA.levels[index];
    return lv.card + "|" + lv.hanzi;
  }

  function saveReview() {
    try {
      localStorage.setItem(REVIEW_STORE, JSON.stringify(state.review.map(reviewKey)));
    } catch (err) {
      /* 无痕窗口 / 跨源限制：存不了就当只在本页有效 */
    }
  }

  function loadReview() {
    let keys = [];
    try {
      keys = JSON.parse(localStorage.getItem(REVIEW_STORE) || "[]");
    } catch (err) {
      return;
    }
    if (!Array.isArray(keys) || !keys.length) return;
    const wanted = new Set(keys);
    state.review = DATA.levels
      .map((lv, i) => (wanted.has(lv.card + "|" + lv.hanzi) ? i : -1))
      .filter((i) => i >= 0); // 找不到的（旧版本里的词）直接丢掉
  }

  function addToReview(index) {
    if (state.review.indexOf(index) < 0) {
      state.review.push(index);
      state.review.sort((a, b) => a - b);
      saveReview();
    }
    updateReviewBtn();
  }

  function dropFromReview(index) {
    const at = state.review.indexOf(index);
    if (at >= 0) {
      state.review.splice(at, 1);
      saveReview();
    }
    updateReviewBtn();
  }

  let clearTimer = null;

  function disarmClear() {
    const btn = $("#review-clear");
    btn.classList.remove("armed");
    btn.textContent = "清空";
    clearTimeout(clearTimer);
    clearTimer = null;
  }

  /* 清空要连点两下：一下是问，两下才真删，避免孩子误碰 */
  function clearReview() {
    const btn = $("#review-clear");
    if (!btn.classList.contains("armed")) {
      btn.classList.add("armed");
      btn.textContent = "再点一次清空";
      clearTimeout(clearTimer);
      clearTimer = setTimeout(disarmClear, 3000);
      return;
    }
    state.review = [];
    saveReview();
    disarmClear();
    updateReviewBtn();
    if (state.mode === "review") exitReview();
    toast("错题本清空啦", "good");
  }

  function updateReviewBtn() {
    const count = state.review.length;
    $("#review-count").textContent = String(count);
    const btn = $("#review-btn");
    btn.disabled = state.demo || count === 0;
    btn.title = count ? `把拖错过的 ${count} 个词再练一遍` : "还没有拖错的词";
    $("#review-clear").hidden = count === 0;
    if (!count) disarmClear();
  }

  function startReview() {
    if (!state.review.length) return;
    if (state.mode === "main") state.mainPos = state.trackPos;
    state.mode = "review";
    state.reviewTrack = state.review.slice();
    state.trackPos = 0;
    startLevel(state.reviewTrack[0]);
    toast(`开始复习 ${state.reviewTrack.length} 个错题`, "good");
  }

  function exitReview() {
    state.mode = "main";
    state.reviewTrack = [];
    const back = Math.max(0, Math.min(ALL_LEVELS.length - 1, state.mainPos));
    state.trackPos = back;
    startLevel(back);
    toast("复习完啦，继续闯关", "good");
  }

  // ------------------------------------------------------------------ render
  function buildLetterPool() {
    const pool = $("#letter-pool");
    pool.innerHTML = "";
    DATA.poolLetters.forEach((letter) => {
      const chip = document.createElement("div");
      chip.className = "chip";
      chip.dataset.letter = letter;
      chip.textContent = letter;
      makeDraggable(chip, { kind: "letter", value: letter });
      pool.appendChild(chip);
    });

    const tones = $("#tone-pool");
    tones.innerHTML = "";
    [1, 2, 3, 4].forEach((n) => {
      const chip = document.createElement("div");
      chip.className = "chip tone";
      chip.dataset.tone = n;
      chip.innerHTML = `<span>${TONE_GLYPH[n]}</span><small>${n}声</small>`;
      makeDraggable(chip, { kind: "tone", value: n });
      tones.appendChild(chip);
    });
  }

  function buildLevelSelect() {
    const select = $("#level-select");
    select.innerHTML = "";
    let currentCard = "";
    let group = null;
    DATA.levels.forEach((lv, i) => {
      if (lv.card !== currentCard) {
        currentCard = lv.card;
        group = document.createElement("optgroup");
        group.label = lv.card;
        select.appendChild(group);
      }
      const option = document.createElement("option");
      option.value = String(i);
      // 只写汉字：拼音要孩子自己拼，不能在这里提前看到答案
      option.textContent = `${i + 1}. ${lv.hanzi}`;
      group.appendChild(option);
    });
    select.addEventListener("change", (ev) => goLevel(Number(ev.target.value)));
  }

  function buildWord() {
    const wrap = $("#word");
    wrap.innerHTML = "";
    const lv = level();
    const matrix = cellMatrix();
    // 两字词放大，三字词自动收窄，保证整行都在卡片里
    wrap.style.setProperty("--cols", String(lv.chars.length));

    lv.chars.forEach((spec, ci) => {
      const st = state.chars[ci];
      const col = document.createElement("div");
      col.className = "char-col";
      col.dataset.char = ci;

      // --- 拼音线（上面）
      const pinyin = document.createElement("div");
      pinyin.className = "pinyin-row";
      spec.syllable.letters.forEach((_, li) => {
        const cell = document.createElement("div");
        cell.className = "letter-wrap";

        if (li === spec.syllable.toneAt) {
          const tone = document.createElement("div");
          tone.className = "tone-slot";
          tone.dataset.char = ci;
          cell.appendChild(tone);
        }

        const slot = document.createElement("div");
        slot.className = "letter-slot";
        slot.dataset.char = ci;
        slot.dataset.letter = li;
        // 空格子：字母要孩子自己从左边挑
        cell.appendChild(slot);

        pinyin.appendChild(cell);
      });
      col.appendChild(pinyin);

      // --- 田字格（下面）
      const box = document.createElement("div");
      box.className = "cell-wrap";
      box.dataset.char = ci;

      const svg = svgEl("svg", { viewBox: `0 0 ${VIEW} ${VIEW}` });

      const grid = svgEl("g", { class: "cell-grid" });
      grid.appendChild(svgEl("rect", { x: 2, y: 2, width: VIEW - 4, height: VIEW - 4, rx: 8 }));
      grid.appendChild(svgEl("line", { class: "dash", x1: VIEW / 2, y1: 0, x2: VIEW / 2, y2: VIEW }));
      grid.appendChild(svgEl("line", { class: "dash", x1: 0, y1: VIEW / 2, x2: VIEW, y2: VIEW / 2 }));
      svg.appendChild(grid);

      const guide = svgEl("g", { class: "guide", transform: matrix });
      (DATA.strokes[spec.char] || []).forEach((d) => guide.appendChild(svgEl("path", { d })));
      svg.appendChild(guide);

      svg.appendChild(svgEl("g", { class: "ink", transform: matrix }));
      box.appendChild(svg);
      col.appendChild(box);

      const meta = document.createElement("div");
      meta.className = "cell-meta";
      meta.innerHTML = `笔画 <b>0</b> / ${st.strokeCount}`;
      col.appendChild(meta);

      wrap.appendChild(col);
    });
  }

  function shuffled(items) {
    const order = items.slice();
    for (let i = order.length - 1; i > 0; i -= 1) {
      const j = Math.floor(Math.random() * (i + 1));
      [order[i], order[j]] = [order[j], order[i]];
    }
    return order;
  }

  function pickDecoys(char, count) {
    const own = new Set(DATA.strokes[char] || []);
    const pool = DECOYS.filter((d) => !own.has(d));
    const picked = [];
    while (picked.length < count && pool.length) {
      picked.push(pool.splice(Math.floor(Math.random() * pool.length), 1)[0]);
    }
    return picked;
  }

  /* 笔画越多，口袋里的干扰笔画越多：8 笔的字混进 5 个错的 */
  function decoyCount(st) {
    const extra = 1 + Math.round(st.strokeCount / 2);
    return Math.max(DATA.decoysPerChar || 0, Math.min(7, extra));
  }

  function buildStrokePool() {
    const pool = $("#stroke-pool");
    pool.innerHTML = "";
    const st = state.chars[state.active];

    updateStrokeNote();

    const real = [];
    for (let i = st.nextStroke; i < st.strokeCount; i += 1) real.push({ kind: "real", index: i });
    const decoys = pickDecoys(st.char, decoyCount(st)).map((path) => ({ kind: "decoy", path }));

    shuffled(real.concat(decoys)).forEach((item) => {
      const chip = document.createElement("div");
      chip.className = "spark";
      if (item.kind === "real") {
        chip.dataset.stroke = item.index;
        chip.appendChild(chipStrokeSvg(DATA.strokes[st.char][item.index]));
        makeDraggable(chip, { kind: "stroke", index: item.index });
      } else {
        chip.dataset.decoy = "1";
        chip.classList.add("decoy");
        chip.appendChild(chipStrokeSvg(item.path));
        makeDraggable(chip, { kind: "stroke", decoy: true });
      }
      pool.appendChild(chip);
    });

    // 笔画多的字块多，按行数收缩每块的大小，整栏不超出一屏
    pool.style.setProperty("--rows", String(Math.ceil(pool.children.length / 3)));
  }

  function syncActive() {
    document.querySelectorAll(".char-col").forEach((col) => {
      const ci = Number(col.dataset.char);
      col.classList.toggle("active", ci === state.active);
      col.classList.toggle("locked", ci !== state.active && !state.chars[ci].done);
    });
    buildStrokePool();
  }

  function hint() {
    const ci = state.active;
    const st = state.chars[ci];

    const li = st.letters.findIndex((v) => !v);
    if (li >= 0) {
      pulse(document.querySelector(`.letter-slot[data-char="${ci}"][data-letter="${li}"]`));
      pulse(document.querySelector(`#letter-pool .chip[data-letter="${st.syllable.letters[li]}"]`));
      return;
    }
    if (st.syllable.tone && !st.tonePlaced) {
      pulse(document.querySelector(`.tone-slot[data-char="${ci}"]`));
      pulse(document.querySelector(`#tone-pool .chip[data-tone="${st.syllable.tone}"]`));
      return;
    }
    const chip = document.querySelector(`#stroke-pool .spark[data-stroke="${st.nextStroke}"]`);
    if (chip) {
      pulse(chip);
      toast(`现在该写第 ${st.nextStroke + 1} 笔`, "good");
    }
  }

  function pulse(node) {
    if (!node) return;
    node.classList.remove("pulse");
    void node.offsetWidth;
    node.classList.add("pulse");
    setTimeout(() => node.classList.remove("pulse"), 2400);
  }

  // -------------------------------------------------------------------- demo
  function flyTo(fromNode, toNode, ms) {
    return new Promise((resolve) => {
      if (!fromNode || !toNode) return resolve();
      const a = fromNode.getBoundingClientRect();
      const b = toNode.getBoundingClientRect();
      const ghost = fromNode.cloneNode(true);
      ghost.classList.add("demo-ghost");
      ghost.classList.remove("dragging", "pulse");
      ghost.style.width = a.width + "px";
      ghost.style.height = a.height + "px";
      ghost.style.fontSize = getComputedStyle(fromNode).fontSize;
      ghost.style.transform = `translate(${a.left}px, ${a.top}px)`;
      document.body.appendChild(ghost);

      toNode.classList.add("hover");
      void ghost.offsetWidth;
      ghost.style.transition = `transform ${ms}ms cubic-bezier(.35,.9,.4,1)`;
      ghost.style.transform = `translate(${b.left + (b.width - a.width) / 2}px, ${
        b.top + (b.height - a.height) / 2
      }px)`;

      setTimeout(() => {
        ghost.remove();
        toNode.classList.remove("hover");
        resolve();
      }, ms + 30);
    });
  }

  async function playDemo() {
    if (state.demo) return;
    startLevel(state.index); // 清空重来，保证从零开始演示
    state.demo = true;
    clearInterval(state.timerId);
    state.timerId = null;
    state.startedAt = 0;
    $("#timer").textContent = "00:00";
    updateReviewBtn();
    document.body.classList.add("demo-mode");
    $("#demo-btn").disabled = true;
    $("#demo-btn").innerHTML = '<span class="play">▶</span>演示中…';
    toast("看好啦，先看我拼一次", "good");
    await sleep(600);

    try {
      for (let ci = 0; ci < state.chars.length; ci += 1) {
        const st = state.chars[ci];
        for (let li = 0; li < st.syllable.letters.length; li += 1) {
          const chip = document.querySelector(`#letter-pool .chip[data-letter="${st.syllable.letters[li]}"]`);
          const slot = document.querySelector(`.letter-slot[data-char="${ci}"][data-letter="${li}"]`);
          await flyTo(chip, slot, 340);
          applyLetter(ci, li);
          await sleep(110);
        }
        if (st.syllable.tone) {
          const chip = document.querySelector(`#tone-pool .chip[data-tone="${st.syllable.tone}"]`);
          const slot = document.querySelector(`.tone-slot[data-char="${ci}"]`);
          await flyTo(chip, slot, 320);
          applyTonePlace(ci);
          await sleep(180);
        }
        for (let s = 0; s < st.strokeCount; s += 1) {
          const chip = document.querySelector(`#stroke-pool .spark[data-stroke="${s}"]`);
          const cell = document.querySelector(`.cell-wrap[data-char="${ci}"]`);
          await flyTo(chip, cell, 300);
          applyStroke(ci, s);
          if (chip) chip.remove();
          await sleep(70);
        }
        checkDone(ci);
        await sleep(260);
      }
      await sleep(500);
    } finally {
      state.demo = false;
      document.body.classList.remove("demo-mode");
      $("#demo-btn").disabled = false;
      $("#demo-btn").innerHTML = '<span class="play">▶</span>看演示';
      startLevel(state.index);
      updateReviewBtn();
      toast("看懂了吗？这次换你来！", "good");
    }
  }

  // -------------------------------------------------------------------- boot
  function init() {
    document.body.appendChild(scratch);
    buildLetterPool();
    buildLevelSelect();
    loadReview();
    startLevel(0);

    $("#hint-btn").addEventListener("click", hint);
    $("#demo-btn").addEventListener("click", playDemo);
    $("#review-btn").addEventListener("click", startReview);
    $("#review-clear").addEventListener("click", clearReview);
    $("#sound-btn").addEventListener("click", (ev) => {
      state.sound = !state.sound;
      ev.currentTarget.textContent = state.sound ? "声音开" : "声音关";
      ev.currentTarget.setAttribute("aria-pressed", String(state.sound));
    });
    $("#replay").addEventListener("click", () => startLevel(state.index));
    $("#next-level").addEventListener("click", nextLevel);
    updateReviewBtn();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
