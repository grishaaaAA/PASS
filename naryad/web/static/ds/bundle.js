/* AUTODISP - поведение компонентов (чистый JS, без зависимостей). Подключать после bundle.css. */
(function () {
  "use strict";
  var doc = document;
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* Волна от точки клика: кнопки, кнопки-иконки, интерактивные карточки, чипы */
  function ripple(el, x, y) {
    if (reduce) return;
    var r = el.getBoundingClientRect();
    var size = Math.max(r.width, r.height) * 2;
    var s = doc.createElement("span");
    s.className = "ad-ripple";
    s.style.width = s.style.height = size + "px";
    s.style.left = (x - r.left - size / 2) + "px";
    s.style.top = (y - r.top - size / 2) + "px";
    el.appendChild(s);
    s.addEventListener("animationend", function () { s.remove(); });
  }

  function closest(t, sel) { return t && t.closest ? t.closest(sel) : null; }

  function activate(target, x, y) {
    var el = closest(target, ".ad-btn, .ad-icon-btn, .ad-card--interactive");
    if (el && !el.disabled && el.getAttribute("aria-disabled") !== "true") {
      if (x === undefined) { var b = el.getBoundingClientRect(); x = b.left + b.width / 2; y = b.top + b.height / 2; }
      ripple(el, x, y);
      if (el.classList.contains("ad-card--interactive")) {
        var on = !el.classList.contains("is-selected");
        el.classList.toggle("is-selected", on);
        el.setAttribute("aria-pressed", on ? "true" : "false");
      }
    }
    /* Строка таблицы: выбор одной строки в пределах tbody */
    var row = closest(target, ".ad-table tbody tr");
    if (row && !closest(target, "a, button, input, select, textarea")) {
      var was = row.getAttribute("aria-selected") === "true";
      var rows = row.parentNode.querySelectorAll('tr[aria-selected="true"]');
      for (var i = 0; i < rows.length; i++) rows[i].removeAttribute("aria-selected");
      if (!was) row.setAttribute("aria-selected", "true");
    }
  }

  doc.addEventListener("click", function (e) { activate(e.target, e.clientX, e.clientY); });
  doc.addEventListener("keydown", function (e) {
    if (e.key !== "Enter" && e.key !== " ") return;
    var el = closest(e.target, ".ad-card--interactive, .ad-table tbody tr[tabindex]");
    if (el && e.target === el) { e.preventDefault(); activate(el); }
  });

  window.AutoDisp = { ripple: ripple, version: "1.0" };
})();
