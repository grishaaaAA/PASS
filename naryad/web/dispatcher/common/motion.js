/*
 * Движение интерфейса AUTODISP - одно на все экраны.
 * Правило: всё, что появляется, исчезает, раскрывается или сменяется, - анимировано.
 * Мягко: появление 320 мс, уход 220 мс, раскрытие списка 420 мс. Кривая появления - плавная
 * (как у выдвижных панелей iOS), ухода - симметричная мягкая, без резкого старта.
 * Анимируем transform и opacity; высоту - только у раскрывающихся списков.
 * При «Уменьшить движение» в системе сдвигов нет, остаётся короткое проявление.
 */
(function () {
  "use strict";
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var EASE = "cubic-bezier(0.32, 0.72, 0, 1)";    // мягкое появление
  var EASE_EXIT = "cubic-bezier(0.4, 0, 0.2, 1)";   // мягкий уход, без резкого старта
  var FAST = 220, BASE = 320, OPEN = 420, CLOSE = 300;

  var FROM = {
    fade: { opacity: 0 },
    up: { opacity: 0, transform: "translateY(12px)" },
    down: { opacity: 0, transform: "translateY(-8px)" },
    scale: { opacity: 0, transform: "scale(0.96)" },
    side: { opacity: 0, transform: "translateX(-8px)" },
    sheet: { opacity: 0, transform: "translateY(24px)" }
  };
  function frames(kind) {
    var f = FROM[kind] || FROM.fade;
    if (reduce) return [{ opacity: 0 }, { opacity: 1 }];
    return [f, { opacity: 1, transform: f.transform ? "none" : undefined }].map(clean);
  }
  function clean(o) { var r = {}; Object.keys(o).forEach(function (k) { if (o[k] !== undefined) r[k] = o[k]; }); return r; }
  function canAnimate(el) { return el && typeof el.animate === "function"; }
  // Завершение анимации: по событию или по таймеру - что раньше. В скрытой вкладке браузер
  // может не прислать событие, а состояние (скрыт, свёрнут, удалён) должно примениться всегда.
  function after(a, ms, fn) {
    var done = false, t = setTimeout(go, ms + 80);
    function go() { if (done) return; done = true; clearTimeout(t); fn(); }
    a.onfinish = go;
    return go;
  }

  // Показать элемент с [hidden]
  function show(el, kind, opts) {
    if (!el) return;
    opts = opts || {};
    var was = el.hidden;
    el.hidden = false;
    if (!canAnimate(el) || (!was && !opts.force)) return;
    if (el._out) { el._out.cancel(); el._out = null; }
    el.animate(frames(kind), { duration: opts.duration || BASE, easing: EASE, delay: opts.delay || 0, fill: "backwards" });
  }
  // Спрятать элемент: сначала уход, потом [hidden]. Уход быстрее появления.
  function hide(el, kind, done) {
    if (!el || el.hidden) { if (done) done(); return; }
    if (!canAnimate(el)) { el.hidden = true; if (done) done(); return; }
    var a = el.animate(frames(kind).reverse(), { duration: FAST, easing: EASE_EXIT, fill: "forwards" });
    el._out = a;
    after(a, FAST, function () { if (el._out !== a) return; el._out = null; el.hidden = true; a.cancel(); if (done) done(); });
  }
  // Вставить элемент (создан кодом) с появлением
  function enter(el, kind, delay) {
    if (!canAnimate(el)) return;
    el.animate(frames(kind), { duration: BASE, easing: EASE, delay: delay || 0, fill: "backwards" });
  }
  // Убрать вставленный элемент с уходом
  function leave(el, kind) {
    if (!el || !canAnimate(el)) { if (el) el.remove(); return; }
    var a = el.animate(frames(kind).reverse(), { duration: FAST, easing: EASE_EXIT, fill: "forwards" });
    after(a, FAST, function () { el.remove(); });
  }
  // Каскад: элементы появляются друг за другом с шагом 40 мс (не больше 8 ступеней)
  function stagger(nodes, kind) {
    Array.prototype.forEach.call(nodes, function (el, i) { enter(el, kind || "up", Math.min(i, 8) * 50); });
  }
  // Смена содержимого на месте: мягкое проявление нового
  function swap(el) { enter(el, "fade"); }

  // Раскрывающиеся списки <details>: плавная высота + проявление содержимого.
  // Перед раскрытием шлём событие details:open - кто строит содержимое лениво, успевает его построить.
  function toggleDetails(d) {
    var sum = d.querySelector(":scope > summary");
    if (!sum) return;
    if (d._anim) { d._anim.cancel(); d._anim = null; }
    var opening = !d.open;
    var start = d.offsetHeight;
    if (opening) { d.open = true; d.classList.remove("is-closing"); d.dispatchEvent(new Event("details:open")); if (d.hasAttribute("data-pin")) pinTop(sum); }
    else d.classList.add("is-closing");  // стрелка поворачивается сразу, не дожидаясь конца анимации
    var border = d.offsetHeight - d.clientHeight;
    var end = opening ? d.offsetHeight : sum.offsetHeight + border;
    if (!canAnimate(d) || reduce) { if (!opening) { d.open = false; d.classList.remove("is-closing"); } return; }
    d.style.overflow = "hidden";
    var a = d.animate([{ height: start + "px" }, { height: end + "px" }], { duration: opening ? OPEN : CLOSE, easing: opening ? EASE : EASE_EXIT });
    d._anim = a;
    // содержимое: при раскрытии проявляется чуть позже, чем начинает расти высота; при сворачивании гаснет первым
    Array.prototype.forEach.call(d.children, function (c) {
      if (c === sum) return;
      if (opening) c.animate([{ opacity: 0, transform: "translateY(-6px)" }, { opacity: 1, transform: "none" }], { duration: BASE, delay: 80, easing: EASE, fill: "backwards" });
      else c.animate([{ opacity: 1 }, { opacity: 0 }], { duration: Math.round(CLOSE * 0.6), easing: EASE_EXIT, fill: "forwards" });
    });
    after(a, opening ? OPEN : CLOSE, function () {
      if (d._anim !== a) return;  // поверх запущена новая анимация - она и завершит
      d._anim = null; d.style.overflow = ""; a.cancel();
      if (!opening) {
        d.open = false; d.classList.remove("is-closing");
        Array.prototype.forEach.call(d.children, function (c) { c.getAnimations().forEach(function (x) { x.cancel(); }); });
      }
    });
  }
  // Раскрытый список остаётся наверху: прокручиваем так, чтобы его заголовок встал к верху области прокрутки
  function pinTop(sum) {
    var box = sum.parentElement;
    while (box && box !== document.body) {
      var oy = getComputedStyle(box).overflowY;
      if ((oy === "auto" || oy === "scroll") && box.scrollHeight > box.clientHeight) break;
      box = box.parentElement;
    }
    if (!box || box === document.body) return;
    var delta = sum.getBoundingClientRect().top - box.getBoundingClientRect().top - 12;
    if (Math.abs(delta) > 4) box.scrollBy({ top: delta, behavior: reduce ? "auto" : "smooth" });
  }
  document.addEventListener("click", function (e) {
    var sum = e.target.closest("summary");
    if (!sum || e.defaultPrevented) return;
    var d = sum.parentElement;
    if (!d || d.tagName !== "DETAILS" || d.hasAttribute("data-static")) return;
    if (e.target.closest("button, a, input, select") && e.target.closest("button, a, input, select") !== sum) return;
    e.preventDefault();
    toggleDetails(d);
  });

  window.Motion = { after: after, reduce: reduce, show: show, hide: hide, enter: enter, leave: leave, stagger: stagger, swap: swap, toggleDetails: toggleDetails, EASE: EASE, EASE_EXIT: EASE_EXIT, BASE: BASE, FAST: FAST };
})();
