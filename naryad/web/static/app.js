/* Генератор на день: парки с вводными -> /api/generate -> результат в тех же строках. */
(function () {
  "use strict";
  var VERSION = "2026-10-02.2";  // как в server.py
  var CLASSES = ["medium", "big", "extra_big"];
  var LABELS = { medium: "средний класс", big: "большой класс", extra_big: "особо большой класс" };
  var MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
    "сентября", "октября", "ноября", "декабря"];
  var WEEKDAYS = ["воскресенье", "понедельник", "вторник", "среда", "четверг", "пятница", "суббота"];
  var $ = function (id) { return document.getElementById(id); };
  var rows = $("rows"), table = $("table");
  var parks = [];          // значения по умолчанию с сервера
  var result = null;       // последний ответ генератора
  var lastRequest = null;  // по каким вводным он получен

  /* Откуда брать данные: сервер на Python (локальный запуск) или генератор внутри страницы (ссылка). */
  function post(path, body) {
    return fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  }
  var API = window.AUTODISP_API || {
    local: true,
    defaults: function () { return fetch("/api/defaults").then(function (r) { return r.json(); }); },
    generate: function (req) {
      return post("/api/generate", req).then(function (r) {
        return r.json().then(function (b) { if (!r.ok) throw new Error(b.error || "сервер не ответил"); return b; });
      });
    },
    save: function (req, format) {
      return post("/api/export", Object.assign({}, req, { format: format })).then(function (r) {
        if (!r.ok) return r.json().then(function (b) { throw new Error(b.error); });
        var name = ((r.headers.get("Content-Disposition") || "").split('filename="')[1] || "autodisp").replace(/"$/, "");
        return r.blob().then(function (blob) {
          var a = document.createElement("a");
          a.href = URL.createObjectURL(blob);
          a.download = name;
          document.body.appendChild(a); a.click(); a.remove();
          setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
        });
      });
    }
  };

  function num(n) { return Number(n).toLocaleString("ru-RU"); }
  function esc(s) {
    return String(s == null ? "-" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function badge(kind, text) { return '<span class="ad-badge ad-badge--' + kind + '">' + esc(text) + "</span>"; }
  function longDate(iso) { var p = iso.split("-"); return +p[2] + " " + MONTHS[+p[1] - 1]; }
  function today() {
    var d = new Date(), m = d.getMonth() + 1, day = d.getDate();
    return d.getFullYear() + "-" + (m < 10 ? "0" : "") + m + "-" + (day < 10 ? "0" : "") + day;
  }
  function weekdayOf(iso) { var p = iso.split("-"); return WEEKDAYS[new Date(+p[0], +p[1] - 1, +p[2]).getDay()]; }

  /* Строки парков */
  function rowHtml(p) {
    var sheet = p.sheet ? '<label class="sub"><input type="checkbox" data-k="sheet"> по справке 12.05.2026</label>' :
      '<span class="sub" title="' + esc(p.address) + '">' + esc(p.address) + "</span>";
    var inputs = CLASSES.map(function (c) {
      return '<td class="ad-num"><input class="ad-input cell" type="number" min="0" max="2000" step="1" data-k="' + c +
        '" aria-label="' + esc(p.name) + ": " + LABELS[c] + '"></td>';
    }).join("");
    return '<tr data-id="' + p.id + '" tabindex="0">' +
      '<td class="check"><input type="checkbox" data-k="enabled" checked aria-label="' + esc(p.name) + '"></td>' +
      '<td class="name"><strong>' + esc(p.name) + "</strong>" + sheet + "</td>" + inputs +
      '<td class="ad-num" data-k="release"></td>' +
      '<td class="ad-num"><input class="ad-input cell" type="number" min="1" max="300" step="1" data-k="routes" aria-label="Маршрутов"></td>' +
      '<td class="ad-num"><input class="ad-input cell" type="number" min="50" max="100" step="0.1" data-k="readiness" aria-label="Исправных, %"></td>' +
      '<td class="ad-num res out" data-r="ok">-</td><td class="ad-num res" data-r="duties">-</td>' +
      '<td class="ad-num res" data-r="drivers">-</td><td class="res" data-r="status">-</td></tr>';
  }

  function field(tr, k) { return tr.querySelector('[data-k="' + k + '"]'); }
  function rowOf(id) { return rows.querySelector('tr[data-id="' + id + '"]'); }

  function fillRow(tr, values) {
    CLASSES.forEach(function (c) { field(tr, c).value = values.classes[c]; });
    field(tr, "routes").value = values.routes;
    field(tr, "readiness").value = values.readiness;
  }

  function applyRowState(tr) {
    var on = field(tr, "enabled").checked;
    var sheet = field(tr, "sheet") && field(tr, "sheet").checked;
    tr.classList.toggle("off", !on);
    tr.querySelectorAll(".cell").forEach(function (i) { i.disabled = !on || sheet; });
    if (field(tr, "sheet")) field(tr, "sheet").disabled = !on;
    var release = CLASSES.reduce(function (s, c) { return s + (+field(tr, c).value || 0); }, 0);
    field(tr, "release").textContent = num(release);
  }

  function reset() {
    rows.innerHTML = parks.map(rowHtml).join("");
    parks.forEach(function (p) { var tr = rowOf(p.id); fillRow(tr, p); applyRowState(tr); });
    syncAll();
    markStale();
  }

  function syncAll() {
    var boxes = rows.querySelectorAll('[data-k="enabled"]');
    var on = Array.prototype.filter.call(boxes, function (b) { return b.checked; }).length;
    $("all").checked = on === boxes.length;
    $("all").indeterminate = on > 0 && on < boxes.length;
    renderTotal();
  }

  function renderTotal() {
    var on = Array.prototype.filter.call(rows.querySelectorAll("tr"), function (tr) { return field(tr, "enabled").checked; });
    var sum = function (k) { return on.reduce(function (s, tr) { return s + (+field(tr, k).value || 0); }, 0); };
    var cells = '<td colspan="2">Итого: ' + on.length + " из " + parks.length + " парков</td>" +
      CLASSES.map(function (c) { return '<td class="ad-num">' + num(sum(c)) + "</td>"; }).join("") +
      '<td class="ad-num">' + num(sum("medium") + sum("big") + sum("extra_big")) + "</td>" +
      '<td class="ad-num">' + num(sum("routes")) + "</td><td></td>";
    var t = result && result.totals;
    cells += t && !isStale() ?
      '<td class="ad-num out">' + num(t.ok) + " / " + num(t.vehicles) + '</td><td class="ad-num">' +
      num(t.line + t.reserve) + '</td><td class="ad-num">' + num(t.ready) + " / " + num(t.shifts) + "</td><td></td>" :
      '<td class="ad-num out">-</td><td class="ad-num">-</td><td class="ad-num">-</td><td></td>';
    $("total").innerHTML = cells;
  }

  /* Вводные -> запрос */
  function collect() {
    return {
      date: $("date").value,
      seed: $("seed").value,
      parks: Array.prototype.map.call(rows.querySelectorAll("tr"), function (tr) {
        var item = { id: tr.dataset.id, enabled: field(tr, "enabled").checked,
          sheet: !!(field(tr, "sheet") && field(tr, "sheet").checked),
          classes: {}, routes: field(tr, "routes").value, readiness: field(tr, "readiness").value };
        CLASSES.forEach(function (c) { item.classes[c] = field(tr, c).value; });
        return item;
      })
    };
  }
  function isStale() { return !lastRequest || JSON.stringify(collect()) !== lastRequest; }
  function markStale() {
    var stale = isStale();
    table.classList.toggle("stale", stale && !!result);
    $("exportJson").disabled = $("exportCsv").disabled = stale;
    if (stale && result) $("sShortSub").textContent = "вводные изменены";
    renderTotal();
  }

  function showError(text) { $("error").hidden = !text; $("error").textContent = text ? "Ошибка: " + text : ""; }

  /* Результат */
  function render(res) {
    var t = res.totals, byId = {};
    res.parks.forEach(function (p) { byId[p.id] = p; });
    $("outHead").textContent = "Результат на " + longDate(res.date);
    $("sBus").textContent = num(t.ok) + " / " + num(t.vehicles);
    $("sBusSub").textContent = "ремонт " + num(t.repair) + ", ТО " + num(t.maintenance);
    $("sDuty").textContent = num(t.line + t.reserve);
    $("sDutySub").textContent = "резерв " + num(t.reserve);
    $("sDrv").textContent = num(t.ready) + " / " + num(t.shifts);
    $("sDrvSub").textContent = "болеют " + num(t.sick) + ", отпуск " + num(t.vacation);
    $("sDrvSub").title = "Больничный " + num(t.sick) + ", отпуск " + num(t.vacation) + ", не прошли медосмотр " + num(t.medical_failed);
    var short = res.parks.filter(function (p) { return p.issues.length; }).length;
    $("sShort").textContent = num(short) + " из " + num(res.parks.length);
    $("sShortSub").textContent = res.weekday + ", набор №" + res.seed;

    rows.querySelectorAll("tr").forEach(function (tr) {
      var p = byId[tr.dataset.id];
      var put = function (k, html) { tr.querySelector('[data-r="' + k + '"]').innerHTML = html; };
      if (!p) {
        ["ok", "duties", "drivers", "status"].forEach(function (k) { put(k, "-"); });
        tr.querySelectorAll(".lack").forEach(function (td) { td.classList.remove("lack"); });
        return;
      }
      put("ok", num(p.ok) + " / " + num(p.vehicles));
      put("duties", num(p.line + p.reserve));
      put("drivers", num(p.ready) + " / " + num(p.shifts));
      var drvShort = p.ready < p.shifts;
      var busShort = p.issues.some(function (s) { return s.indexOf("класс") >= 0; }) || p.ok < p.line + p.reserve;
      tr.querySelector('[data-r="ok"]').classList.toggle("lack", busShort);
      tr.querySelector('[data-r="drivers"]').classList.toggle("lack", drvShort);
      put("status", p.issues.length ? badge("warning", "Нехватка") : badge("success", "В норме"));
    });
    renderIssues();
  }

  function renderIssues() {
    var box = $("issues");
    if (!result) return;
    var selected = rows.querySelector('tr[aria-selected="true"]');
    var list = result.parks.filter(function (p) { return !selected || p.id === selected.dataset.id; });
    var names = {};
    parks.forEach(function (p) { names[p.id] = p.name; });
    var withIssues = list.filter(function (p) { return p.issues.length; });
    var html = withIssues.length ? withIssues.map(function (p) {
      return "<h3>" + esc(names[p.id]) + "</h3><ul>" + p.issues.map(function (s) {
        return "<li>" + esc(s.replace(names[p.id] + ": ", "")) + "</li>";
      }).join("") + "</ul>";
    }).join("") : '<p class="body muted">' + (selected ? esc(names[selected.dataset.id]) + ": автобусов и водителей хватает." :
      "Автобусов и водителей хватает во всех выбранных парках.") + "</p>";
    html += '<details><summary>Как генератор заполняет то, чего нет во вводных (' + result.assumptions.length +
      ")</summary><ul>" + result.assumptions.map(function (a) { return "<li>" + esc(a) + "</li>"; }).join("") + "</ul></details>";
    box.innerHTML = html;
  }

  /* События */
  rows.addEventListener("click", function (e) {
    if (e.target.closest("label, input")) { e.stopPropagation(); return; } // не выделять строку при правке
    setTimeout(renderIssues, 0);  // выделение строки ставит bundle.js на уровне документа
  });
  rows.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") setTimeout(renderIssues, 0); });
  rows.addEventListener("input", function (e) { applyRowState(e.target.closest("tr")); markStale(); });
  rows.addEventListener("change", function (e) {
    var tr = e.target.closest("tr"), k = e.target.dataset.k;
    if (k === "sheet") {
      var p = parks.filter(function (x) { return x.id === tr.dataset.id; })[0];
      fillRow(tr, e.target.checked ? p.sheet : p);
    }
    applyRowState(tr);
    if (k === "enabled") syncAll();
    markStale();
  });
  $("all").addEventListener("change", function () {
    var on = $("all").checked;
    rows.querySelectorAll("tr").forEach(function (tr) { field(tr, "enabled").checked = on; applyRowState(tr); });
    syncAll();
    markStale();
  });
  $("date").addEventListener("input", function () { $("weekday").textContent = $("date").value ? weekdayOf($("date").value) : ""; markStale(); });
  $("seed").addEventListener("input", markStale);
  $("reset").addEventListener("click", reset);

  $("run").addEventListener("click", function () {
    var request = collect(), btn = $("run");
    showError("");
    btn.disabled = true;
    btn.querySelector("span").textContent = "Генерируем…";
    API.generate(request)
      .then(function (body) {
        result = body;
        lastRequest = JSON.stringify(request);
        render(result);
        markStale();
      })
      .catch(function (err) { showError(err.message === "Failed to fetch" ? "сервер генератора не запущен" : err.message); })
      .then(function () { btn.disabled = false; btn.querySelector("span").textContent = "Сгенерировать"; });
  });

  function download(format, btn) {
    btn.disabled = true;
    API.save(JSON.parse(lastRequest), format)
      .catch(function (err) { if (err && err.message) showError(err.message); })
      .then(function () { markStale(); });
  }
  $("exportJson").addEventListener("click", function () { download("json", this); });
  $("exportCsv").addEventListener("click", function () { download("csv", this); });

  $("date").value = today();
  $("weekday").textContent = weekdayOf($("date").value);
  function fatal(title, html) {
    $("fatal").hidden = false;
    $("fatal").innerHTML = esc(title) + "<p>" + html + "</p>";
  }
  if (API.local && location.protocol === "file:") {
    fatal("Страница открыта как файл, а не через сервер",
      "Запустите <code>start.bat</code> (Windows) или <code>start.command</code> (Mac) в папке проекта - браузер откроется сам.");
    return;
  }
  API.defaults().then(function (d) {
    if (d.version !== VERSION || !d.parks) {
      fatal("Запущен старый сервер генератора",
        "Закройте все окна с сервером (или нажмите в них Ctrl+C) и запустите заново <code>start.bat</code> / <code>start.command</code>.");
      return;
    }
    parks = d.parks;
    reset();
  }).catch(function () {
    fatal("Сервер генератора не отвечает",
      "Запустите <code>start.bat</code> (Windows) или <code>start.command</code> (Mac) в папке проекта и не закрывайте окно сервера.");
  });
})();
