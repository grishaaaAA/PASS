/*
 * Загрузка реестра перевозчика файлом: CSV (таблица на файл) или книга Excel.
 * «Проверить» - POST /api/days/import с dry_run: отчёт, что чем понято, день не создаётся.
 * «Открыть реестр» - та же загрузка без dry_run и GET /api/days/{id}: данные дня уходят на
 * экран подготовки так же, как реестр парка с сервера. Этот промежуточный день сервер забудет
 * сам, когда диспетчер нажмёт «Рассчитать» (тот же день тех же парков заменяет прежний).
 */
(function () {
  "use strict";
  var D = window.Disp, esc = D.esc;
  var MAX_FILES = 24, MAX_BYTES = 45 * 1024 * 1024;  // сервер берёт запрос до 64 МБ, а base64 толще файла на треть
  var ENTITIES = [["parks", "Парки"], ["routes", "Маршруты"], ["duties", "Наряды"], ["shifts", "Смены"], ["vehicles", "Автобусы"], ["drivers", "Водители"]];
  // Поля набора данных так, как их называет перевозчик (naryad/data/registry.py, COLUMNS)
  var FIELDS = {
    parks: { id: "код парка", name: "название", address: "адрес", lat: "широта", lon: "долгота", state: "состояние", list_count: "списочное количество",
      tech_readiness: "техническая готовность", release_weekday: "выпуск в будни", release_weekend: "выпуск в выходные",
      reserve_weekday: "резерв в будни", reserve_weekend: "резерв в выходные" },
    routes: { id: "код маршрута", park_id: "парк", number: "номер", allowed_classes: "допустимые классы", priority: "важность", length_km: "длина",
      length_source: "источник длины", turnaround_min: "время оборота", duties_weekday: "нарядов в будни", duties_weekend: "нарядов в выходные" },
    duties: { id: "код наряда", park_id: "парк", type: "тип наряда", route_id: "маршрут", vehicle_class: "класс автобуса", day_type: "тип дня",
      start: "начало", end: "окончание", shift_count: "количество смен" },
    shifts: { id: "код смены", duty_id: "наряд", order: "номер смены", start: "начало", end: "окончание", start_place: "место начала" },
    vehicles: { id: "код автобуса", board_number: "гаражный номер", plate: "госномер", model: "модель", make: "марка", class: "класс", fuel: "топливо",
      park_id: "парк", condition: "состояние", repair_days_left: "дней ремонта", home_route_id: "закреплённый маршрут" },
    drivers: { id: "код водителя", tab_number: "табельный номер", full_name: "ФИО", park_id: "парк", classes: "допуск к классам", makes: "обкатан на",
      routes: "маршруты", home_vehicle_id: "закреплённый автобус", schedule: "график", medical: "медосмотр" }
  };
  function fields(entity, list) { return list.map(function (f) { return (FIELDS[entity] && FIELDS[entity][f]) || f; }).join(", "); }

  function ext(f) { var m = /\.([^.]+)$/.exec(f.name); return m ? m[1].toLowerCase() : ""; }
  function isBook(f) { return ext(f) === "xlsx"; }
  function size(n) { return n < 1024 * 1024 ? Math.max(1, Math.round(n / 1024)) + " КБ" : (Math.round(n / 104857.6) / 10).toLocaleString("ru-RU") + " МБ"; }
  function today() { var d = new Date(), p = function (n) { return (n < 10 ? "0" : "") + n; }; return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()); }
  function dayType(date) { var p = date.split("-"), w = new Date(+p[0], +p[1] - 1, +p[2]).getDay(); return w === 0 || w === 6 ? "weekend" : "weekday"; }

  // Что не так с набором файлов, или null
  function problem(files) {
    if (files.some(function (f) { return ext(f) === "xls"; })) return "Старый формат .xls не читается: сохраните книгу как .xlsx";
    var other = files.filter(function (f) { return ["csv", "txt", "xlsx"].indexOf(ext(f)) < 0; });
    if (other.length) return "Подходят только CSV и книги Excel (.xlsx), а «" + other[0].name + "» - нет";
    if (files.filter(isBook).length > 1) return "Книга Excel - одна за раз, остальные таблицы можно добавить файлами CSV";
    if (files.length > MAX_FILES) return "Не больше " + MAX_FILES + " файлов за раз";
    var total = files.reduce(function (s, f) { return s + f.size; }, 0);
    if (total > MAX_BYTES) return "Файлы вместе больше " + size(MAX_BYTES) + ": загрузите реестр частями или без лишних листов";
    return null;
  }

  function read(f) {
    return new Promise(function (resolve, reject) {
      var r = new FileReader();
      r.onload = function () { resolve(r.result); };
      r.onerror = function () { reject(new Error("Файл «" + f.name + "» не читается")); };
      r.readAsArrayBuffer(f);
    });
  }
  // CSV из Excel в русской Windows - в кодировке 1251, а не UTF-8
  function text(buf) {
    try { return new TextDecoder("utf-8", { fatal: true }).decode(buf); } catch (e) { return new TextDecoder("windows-1251").decode(buf); }
  }
  function base64(buf) {
    var bytes = new Uint8Array(buf), out = "";
    for (var i = 0; i < bytes.length; i += 0x8000) out += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(out);
  }
  function request(files, meta, dry) {
    return Promise.all(files.map(read)).then(function (bufs) {
      var body = { meta: meta, dry_run: dry }, csv = {};
      files.forEach(function (f, i) { if (isBook(f)) body.workbook = base64(bufs[i]); else csv[f.name] = text(bufs[i]); });
      if (Object.keys(csv).length) body.files = csv;
      return D.api("POST", "/api/days/import", body);
    });
  }

  // Отчёт о загрузке: итог, ошибки, по каждой таблице - что понято, что пропущено, чего нет.
  function reportHtml(rep) {
    var errors = rep.errors || [], warnings = rep.warnings || [], n = errors.length;
    var html = n
      ? '<div class="up__verdict up__verdict--bad"><i class="ad-icon ad-icon--x-circle"></i><b>Реестр не принят: ' + n + " " + D.plural(n, "ошибка", "ошибки", "ошибок") + "</b></div>" +
        '<ul class="up__list">' + errors.map(function (e) { return "<li>" + esc(e) + "</li>"; }).join("") + "</ul>"
      : '<div class="up__verdict"><i class="ad-icon ad-icon--check-circle"></i><b>Реестр прочитан</b></div>';
    var by = {};
    (rep.entities || []).forEach(function (e) { by[e.entity] = e; });
    html += '<div class="up__ents">' + ENTITIES.filter(function (x) { return by[x[0]]; }).map(function (x) {
      // понятые столбцы - в порядке полей сущности, а не как пришли
      var e = by[x[0]], matched = e.matched || {}, order = Object.keys(FIELDS[x[0]] || {});
      var cols = Object.keys(matched).sort(function (a, b) { return (order.indexOf(a) + 1 || 99) - (order.indexOf(b) + 1 || 99); }).map(function (k) { return matched[k]; });
      var src = e.source && [x[0], x[1].toLowerCase()].indexOf(String(e.source).toLowerCase()) < 0 ? e.source : "";
      var rows = [
        ["Понято", cols.join(", ")],
        e.missing && e.missing.length ? ["Нет столбцов", fields(x[0], e.missing), "bad"] : null,
        e.unknown_columns && e.unknown_columns.length ? ["Пропущено", e.unknown_columns.join(", ")] : null,
        e.derived && e.derived.length ? ["Собрано из других", fields(x[0], e.derived)] : null,
        e.defaulted && e.defaulted.length ? ["Заполнено само", fields(x[0], e.defaulted)] : null
      ].filter(Boolean);
      var bad = e.missing && e.missing.length;
      return '<details class="grp up__grp"><summary><span class="grp__t">' + x[1] + (src ? ' <em class="up__src">«' + esc(src) + "»</em>" : "") + "</span>" +
        // без обязательного столбца строки не читаются - «0 строк» было бы неправдой
        '<span class="grp__n' + (bad ? " up__bad" : "") + '">' + (bad ? "нет столбцов: " + e.missing.length : D.num(e.rows) + " " + D.plural(e.rows, "строка", "строки", "строк")) + "</span>" +
        '<i class="ad-icon ad-icon--caret-down ad-icon--sm grp__car"></i></summary><dl class="up__dl">' +
        rows.map(function (r) { return "<dt>" + r[0] + "</dt><dd" + (r[2] ? ' class="up__bad"' : "") + ">" + esc(r[1] || "-") + "</dd>"; }).join("") + "</dl></details>";
    }).join("") + "</div>";
    if (warnings.length) {
      html += '<details class="grp up__grp"><summary><span class="grp__t">Предупреждения</span><span class="grp__n">' + warnings.length + "</span>" +
        '<i class="ad-icon ad-icon--caret-down ad-icon--sm grp__car"></i></summary><ul class="up__list up__list--in">' +
        warnings.map(function (w) { return "<li>" + esc(w) + "</li>"; }).join("") + "</ul></details>";
    }
    return html;
  }

  // Окно загрузки. onOpened(day, name) - данные дня и короткое название источника для экрана подготовки.
  function open(onOpened) {
    var box = document.createElement("div");
    box.className = "modal";
    box.innerHTML =
      '<div class="modal__box" role="dialog" aria-modal="true" aria-labelledby="upH">' +
      '<div class="modal__head"><div><div class="caption muted">Реестр перевозчика</div><h3 class="ev__h" id="upH">Загрузить реестр</h3></div>' +
      '<button type="button" class="btn-close" data-close aria-label="Закрыть"></button></div>' +
      '<label class="up__drop" data-drop><input class="up__in" type="file" multiple accept=".csv,.txt,.xlsx" data-file>' +
      '<span class="up__ic"><i class="ad-icon ad-icon--upload-simple"></i></span>' +
      '<span class="up__t"><b>Выберите файлы</b> или перетащите сюда</span>' +
      '<span class="up__s">Таблицы CSV или книга Excel (.xlsx)</span></label>' +
      '<div class="up__files" data-files hidden></div>' +
      '<div class="ev__form up__form">' +
      '<label class="ad-field"><span class="ad-field__label">Дата</span><input class="ad-input" type="date" name="date" required></label>' +
      '<label class="ad-field"><span class="ad-field__label">День</span><select class="ad-input" name="dt"><option value="weekday">Будний</option><option value="weekend">Выходной</option></select></label></div>' +
      '<div class="up__report" data-report></div>' +
      '<div class="cf__actions"><button type="button" class="ad-btn ad-btn--primary" data-run disabled>Проверить</button></div></div>';
    document.body.appendChild(box);
    window.Motion.enter(box, "fade"); window.Motion.enter(box.firstChild, "scale");

    var $ = function (sel) { return box.querySelector(sel); };
    var input = $("[data-file]"), drop = $("[data-drop]"), list = $("[data-files]"), dateIn = $("[name=date]"), dtSel = $("[name=dt]");
    var out = $("[data-report]"), run = $("[data-run]");
    var files = [], checked = false, working = false;
    dateIn.value = today(); dtSel.value = dayType(dateIn.value);

    var close = function () { window.Motion.leave(box.firstChild, "scale"); window.Motion.leave(box, "fade"); document.removeEventListener("keydown", onKey); };
    var onKey = function (e) { if (e.key === "Escape" && !working) close(); };
    document.addEventListener("keydown", onKey);
    box.addEventListener("click", function (e) { if (!working && (e.target === box || e.target.closest("[data-close]"))) close(); });

    function sync() {
      run.disabled = working || !files.length || !dateIn.value;
      if (!working) run.textContent = checked ? "Открыть реестр" : "Проверить";
    }
    function show(html) { out.innerHTML = html; if (html) window.Motion.swap(out); }
    // Любая перемена - файлы, дата, тип дня - и прежний отчёт уже не про то
    function changed() { checked = false; show(""); sync(); }

    function take(picked) {
      picked = Array.prototype.slice.call(picked || []);
      if (!picked.length) return;
      var bad = problem(picked);
      files = bad ? [] : picked;
      list.hidden = !files.length;
      list.innerHTML = files.map(function (f) {
        return '<span class="up__file"><i class="ad-icon ad-icon--file-text ad-icon--sm"></i>' + esc(f.name) + '<span class="muted">' + size(f.size) + "</span></span>";
      }).join("");
      if (files.length) window.Motion.stagger(list.children, "up");
      changed();
      if (bad) show('<p class="ev__err">' + esc(bad) + "</p>");
    }
    input.addEventListener("change", function () { take(input.files); input.value = ""; });
    ["dragenter", "dragover"].forEach(function (t) { drop.addEventListener(t, function (e) { e.preventDefault(); drop.classList.add("is-over"); }); });
    ["dragleave", "drop"].forEach(function (t) { drop.addEventListener(t, function (e) { e.preventDefault(); drop.classList.remove("is-over"); }); });
    drop.addEventListener("drop", function (e) { take(e.dataTransfer && e.dataTransfer.files); });
    dateIn.addEventListener("change", function () { if (dateIn.value) dtSel.value = dayType(dateIn.value); changed(); });
    dtSel.addEventListener("change", changed);

    function busy(text) {
      working = !!text; sync();
      if (text) run.innerHTML = '<span class="spinner"></span> ' + esc(text);
    }
    run.addEventListener("click", function () {
      var meta = { date: dateIn.value, day_type: dtSel.value, moment: "morning" };
      if (!checked) {
        busy("Проверяю…");
        request(files, meta, true).then(function (res) {
          busy(""); checked = true; sync(); show(reportHtml(res.report));
        }, function (e) {
          busy("");
          show(e.body && e.body.report ? reportHtml(e.body.report) : '<p class="ev__err">' + esc(e.message) + "</p>");
        });
        return;
      }
      busy("Открываю…");
      request(files, meta, false).then(function (res) {
        return D.api("GET", "/api/days/" + encodeURIComponent(res.day_id));
      }).then(function (day) {
        working = false; close();
        onOpened(day, files.length === 1 ? files[0].name : files.length + " " + D.plural(files.length, "файл", "файла", "файлов"));
      }, function (e) {
        busy(""); checked = false; sync();
        show(e.body && e.body.report ? reportHtml(e.body.report) : '<p class="ev__err">' + esc(e.message) + "</p>");
      });
    });
    sync();
  }

  D.upload = { open: open };
})();
