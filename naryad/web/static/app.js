/* Страница генератора: вводные -> /api/generate -> сводка; выгрузка - ссылки на /api/export. */
(function () {
  "use strict";
  var form = document.getElementById("form");
  var results = document.getElementById("results");
  var runBtn = document.getElementById("run");
  var errorBox = document.getElementById("formError");
  var city = document.getElementById("city");
  var DEFAULTS = null;
  var MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
    "сентября", "октября", "ноября", "декабря"];
  var PRESET_HINTS = {
    case: "Вводные заказчика: 7 площадок, 118 маршрутов, выпуск 270 на парк, классы 700 / 700 / 500.",
    park7: "Реальный парк №7 по справке: 420 автобусов, 24 маршрута, выпуск 350 в будни и 266 в выходной."
  };

  function num(n) { return Number(n).toLocaleString("ru-RU"); }
  function esc(s) {
    return String(s == null ? "-" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function longDate(iso) { var p = iso.split("-"); return +p[2] + " " + MONTHS[+p[1] - 1]; }
  function shortDate(iso) { var p = iso.split("-"); return p[2] + "." + p[1] + "." + p[0]; }
  function badge(kind, text) { return '<span class="ad-badge ad-badge--' + kind + '">' + esc(text) + "</span>"; }

  /* Переключатели */
  function segValue(name) {
    var on = form.querySelector('.seg[data-name="' + name + '"] [aria-checked="true"]');
    return on ? on.dataset.value : null;
  }
  function setSeg(name, value) {
    form.querySelectorAll('.seg[data-name="' + name + '"] [role="radio"]').forEach(function (b) {
      b.setAttribute("aria-checked", b.dataset.value === value ? "true" : "false");
      b.tabIndex = b.dataset.value === value ? 0 : -1;
    });
    if (name === "preset") applyPreset();
  }
  form.querySelectorAll(".seg").forEach(function (seg) {
    var name = seg.dataset.name;
    seg.addEventListener("click", function (e) {
      var b = e.target.closest('[role="radio"]');
      if (b) setSeg(name, b.dataset.value);
    });
    seg.addEventListener("keydown", function (e) {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      var items = Array.prototype.slice.call(seg.querySelectorAll('[role="radio"]'));
      var i = items.findIndex(function (b) { return b.getAttribute("aria-checked") === "true"; });
      var next = items[(i + (e.key === "ArrowRight" ? 1 : items.length - 1)) % items.length];
      setSeg(name, next.dataset.value);
      next.focus();
      e.preventDefault();
    });
    setSeg(name, segValue(name));
  });

  function applyPreset() {
    var park7 = segValue("preset") === "park7";
    city.disabled = park7;
    document.getElementById("cityLocked").hidden = !park7;
    document.getElementById("presetHint").textContent = PRESET_HINTS[park7 ? "park7" : "case"];
  }

  function fillDefaults() {
    if (!DEFAULTS) return;
    var c = DEFAULTS.case;
    form.park_count.value = c.park_count;
    form.release_per_park.value = c.release_per_park;
    form.routes_total.value = c.routes_total;
    form.tech_readiness.value = c.tech_readiness;
    form.weekend_factor.value = c.weekend_factor;
    form.mix_medium.value = c.class_mix.medium;
    form.mix_big.value = c.class_mix.big;
    form.mix_extra_big.value = c.class_mix.extra_big;
    form.days.max = DEFAULTS.max_days;
    document.getElementById("daysHint").textContent = "От 1 до " + DEFAULTS.max_days;
  }

  function collect() {
    var data = {
      preset: segValue("preset"),
      moment: segValue("moment"),
      start: form.start.value,
      days: form.days.value,
      seed: form.seed.value
    };
    if (data.preset === "case") {
      ["park_count", "release_per_park", "routes_total", "tech_readiness", "weekend_factor",
        "mix_medium", "mix_big", "mix_extra_big"].forEach(function (k) { data[k] = form[k].value; });
    }
    return data;
  }

  function showError(text) {
    errorBox.hidden = !text;
    errorBox.textContent = text ? "Ошибка: " + text : "";
  }

  function exportUrl(params, format) {
    var q = new URLSearchParams();
    Object.keys(params).forEach(function (k) { if (params[k] !== "" && params[k] != null) q.set(k, params[k]); });
    q.set("format", format);
    return "/api/export?" + q.toString();
  }

  /* Отрисовка результата */
  function render(res, params) {
    var days = res.days, first = days[0];
    var short = days.filter(function (d) { return d.warning_count > 0; }).length;
    var errors = days.reduce(function (s, d) { return s + d.error_count; }, 0);
    var html = "";

    html += '<div class="kpis">' +
      kpi(0, "bus", "Автобусы", num(first.ok) + " / " + num(first.vehicles), "исправны / по списку",
        badge("neutral", "Ремонт " + num(first.repair) + ", ТО " + num(first.maintenance))) +
      kpi(1, "clipboard-text", "Наряды", num(first.line + first.reserve), "на " + longDate(first.date),
        badge("info", "Резерв " + num(first.reserve))) +
      kpi(2, "users", "Водители", num(first.ready) + " / " + num(first.shifts), "готовы / смен",
        first.ready >= first.shifts ? badge("success", "Хватает") :
          badge("warning", "Мало на " + num(first.shifts - first.ready))) +
      kpi(3, "calendar-dots", "Нехватка", num(short) + " / " + num(days.length), "дней с нехваткой",
        errors ? badge("danger", "Ошибок: " + num(errors)) : badge("success", "Данные целы")) +
      "</div>";

    html += '<div class="ad-card block ad-appear" style="--i:4"><div class="block__head">' +
      '<h2 class="heading-3">Выгрузка</h2>' +
      '<ul class="facts"><li>Парков <b>' + num(res.parks.length) + "</b></li><li>Маршрутов <b>" +
      num(res.routes) + "</b></li><li>Дней <b>" + num(days.length) + "</b></li><li>Номер набора <b>" +
      esc(res.meta.seed) + "</b></li></ul></div>" +
      '<div class="exports">' +
      '<a class="ad-btn ad-btn--secondary" href="' + exportUrl(params, "series_zip") + '" download>' +
      '<i class="ad-icon ad-icon--download-simple ad-icon--sm" aria-hidden="true"></i>Все дни - ZIP (' +
      num(days.length) + " " + plural(days.length, "файл", "файла", "файлов") + " + сводка)</a>" +
      '<a class="ad-btn ad-btn--outline" href="' + exportUrl(params, "day_json") + '" download>' +
      '<i class="ad-icon ad-icon--file-text ad-icon--sm" aria-hidden="true"></i>' + esc(longDate(first.date)) + " - JSON</a>" +
      '<a class="ad-btn ad-btn--outline" href="' + exportUrl(params, "day_csv") + '" download>' +
      '<i class="ad-icon ad-icon--export ad-icon--sm" aria-hidden="true"></i>' + esc(longDate(first.date)) + " - CSV для Excel</a>" +
      "</div>" +
      '<p class="hint">Архив собирается заново по тем же вводным и номеру набора, поэтому совпадает со сводкой. На 30 дней кейса это около 7 МБ и до 10 секунд.</p>' +
      "</div>";

    html += '<div class="ad-card block ad-appear" style="--i:5"><div class="block__head">' +
      '<h2 class="heading-3">По дням</h2><span class="hint">Нажмите на строку, чтобы увидеть, чего не хватает</span></div>' +
      '<div class="ad-table-wrap"><table class="ad-table" id="daysTable"><thead><tr>' +
      '<th>Дата</th><th>День</th><th class="ad-num">Исправны</th><th class="ad-num">Нужно автобусов</th>' +
      '<th class="ad-num">Водителей готово</th><th class="ad-num">Смен</th><th class="ad-num">Больничный</th>' +
      "<th>Статус</th></tr></thead><tbody>" +
      days.map(function (d, i) {
        var status = d.error_count ? badge("danger", "Ошибки в данных") :
          d.warning_count ? badge("warning", "Нехватка: " + d.warning_count) : badge("success", "Без нехватки");
        return '<tr tabindex="0" data-i="' + i + '"><td>' + shortDate(d.date) + "</td><td>" +
          (d.day_type === "weekday" ? "будни" : "выходной") + '</td><td class="ad-num">' + num(d.ok) +
          '</td><td class="ad-num">' + num(d.line + d.reserve) + '</td><td class="ad-num">' + num(d.ready) +
          '</td><td class="ad-num">' + num(d.shifts) + '</td><td class="ad-num">' + num(d.sick) +
          "</td><td>" + status + "</td></tr>";
      }).join("") + "</tbody></table></div>" +
      '<div id="dayDetail"></div></div>';

    html += '<div class="ad-card block ad-appear" style="--i:6"><div class="block__head">' +
      '<h2 class="heading-3">По паркам</h2><span class="hint">' + esc(longDate(first.date)) + "</span></div>" +
      '<div class="ad-table-wrap"><table class="ad-table"><thead><tr><th>Парк</th>' +
      '<th class="ad-num">Маршрутов</th><th class="ad-num">Исправны / по списку</th><th class="ad-num">Нарядов</th>' +
      '<th class="ad-num">Водителей готово / смен</th><th>Статус</th></tr></thead><tbody>' +
      res.parks.map(function (p) {
        var lackBus = (p.line + p.reserve) - p.ok, lackDrv = p.shifts - p.ready;
        var status = lackDrv > 0 ? badge("warning", "Не хватает водителей: " + lackDrv) :
          lackBus > 0 ? badge("warning", "Не хватает автобусов: " + lackBus) : badge("success", "В норме");
        return '<tr><td class="wrap"><strong>' + esc(p.name) + '</strong><span class="sub">' + esc(p.address) +
          '</span></td><td class="ad-num">' + num(p.routes) + '</td><td class="ad-num">' + num(p.ok) + " / " +
          num(p.vehicles) + '</td><td class="ad-num">' + num(p.line + p.reserve) + '</td><td class="ad-num">' +
          num(p.ready) + " / " + num(p.shifts) + "</td><td>" + status + "</td></tr>";
      }).join("") + "</tbody></table></div></div>";

    html += '<div class="ad-card block ad-appear" style="--i:7"><details class="assume"><summary>' +
      "Допущения генератора (" + res.meta.assumptions.length + ")</summary><ul>" +
      res.meta.assumptions.map(function (a) { return "<li>" + esc(a) + "</li>"; }).join("") +
      "</ul></details><p class=\"hint\">Источник: " + esc(res.meta.source) + "</p></div>";

    results.innerHTML = html;
    var table = document.getElementById("daysTable");
    /* Выбор строки ставит bundle.js на уровне документа - читаем его после него */
    table.addEventListener("click", function (e) {
      var row = e.target.closest("tr[data-i]");
      setTimeout(function () { showDay(row); }, 0);
    });
    table.addEventListener("keydown", function (e) {
      if (e.key !== "Enter" && e.key !== " ") return;
      var row = e.target.closest("tr[data-i]");
      setTimeout(function () { showDay(row); }, 0);
    });

    function showDay(row) {
      var box = document.getElementById("dayDetail");
      if (!row) return;
      if (row.getAttribute("aria-selected") !== "true") { box.innerHTML = ""; return; }
      var d = days[+row.dataset.i];
      var items = d.errors.concat(d.warnings);
      box.innerHTML = '<h3 class="title" style="margin:0 0 8px">' + esc(longDate(d.date)) + "</h3>" +
        (items.length ? '<ul class="day-detail">' + items.map(function (w) { return "<li>" + esc(w) + "</li>"; }).join("") + "</ul>"
          : '<p class="body muted" style="margin:0">Автобусов и водителей хватает во всех парках.</p>');
    }
  }

  function kpi(i, icon, title, value, note, footer) {
    return '<div class="ad-card ad-appear" style="--i:' + i + '"><span class="ad-card__icon">' +
      '<i class="ad-icon ad-icon--' + icon + '" aria-hidden="true"></i></span>' +
      '<h3 class="ad-card__title">' + esc(title) + '</h3><p class="ad-card__value">' + esc(value) +
      '</p><p class="caption">' + esc(note) + '</p><div class="ad-card__footer">' + footer + "</div></div>";
  }

  function plural(n, one, few, many) {
    var a = n % 10, b = n % 100;
    if (a === 1 && b !== 11) return one;
    if (a >= 2 && a <= 4 && (b < 12 || b > 14)) return few;
    return many;
  }

  /* Запуск */
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var params = collect();
    showError("");
    runBtn.disabled = true;
    runBtn.querySelector("span").textContent = "Генерируем…";
    results.setAttribute("aria-busy", "true");
    results.innerHTML = document.getElementById("skeleton").innerHTML;
    fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params)
    }).then(function (r) {
      return r.json().then(function (body) { return { ok: r.ok, body: body }; });
    }).then(function (r) {
      if (!r.ok) throw new Error(r.body.error || "сервер не ответил");
      render(r.body, params);
    }).catch(function (err) {
      showError(err.message === "Failed to fetch" ? "сервер генератора не запущен" : err.message);
      results.innerHTML = '<div class="ad-card empty"><h2 class="heading-3">Данные не собраны</h2>' +
        '<p class="body muted">Исправьте вводные слева и нажмите «Сгенерировать» ещё раз.</p></div>';
    }).then(function () {
      runBtn.disabled = false;
      runBtn.querySelector("span").textContent = "Сгенерировать";
      results.removeAttribute("aria-busy");
    });
  });

  document.getElementById("reset").addEventListener("click", function () {
    form.start.value = "2026-10-05";
    form.days.value = 7;
    form.seed.value = 1;
    setSeg("preset", "case");
    setSeg("moment", "morning");
    fillDefaults();
    showError("");
  });

  fetch("/api/defaults").then(function (r) { return r.json(); }).then(function (d) {
    DEFAULTS = d;
    fillDefaults();
  }).catch(function () { showError("сервер генератора не запущен"); });
})();
