/*
 * Выбор парков и подготовка дня. Главный экран - все парки (пока доступен только №7).
 * Подготовка: реестр парка на утро с сервера, диспетчер отмечает, что сегодня работает:
 * маршруты, состояние автобусов, график и медосмотр водителей. Отметки хранятся в браузере
 * по парку, в реестр не записываются. «Рассчитать» собирает день и отдаёт его движку.
 * К дате пока не привязываемся: реестр берётся на сегодня.
 */
(function () {
  "use strict";
  var D = window.Disp, $ = D.$, esc = D.esc, toast = D.events.toast;
  var M = window.Motion, Ops = window.Operations;
  var signedIn = false;
  var entryHash = location.hash;
  // file - название реестра, загруженного файлом (иначе null: реестр парка с сервера), fileParks - его парки
  var P = { parks: [], sel: [], ids: [], park: null, date: null, base: null, file: null, fileParks: {}, ov: null, open: {}, tab: null, filter: { veh: "all", drv: "all" }, st: null, saved: null,
    sort: { veh: { key: "route", dir: 1 }, drv: { key: "name", dir: 1 } }, q: { veh: "", drv: "" } };

  var aiButton = $("#btnAi"), aiChat = $("#aiChat"), aiInput = $("#aiChatInput"), aiMessages = $("#aiChatMessages");
  var aiForm = $("#aiChatForm"), aiSend = $("#btnAiSend");
  var aiDate = new Intl.DateTimeFormat("ru", { day: "numeric", month: "long" });
  var aiTime = new Intl.DateTimeFormat("ru", { hour: "2-digit", minute: "2-digit" });
  function closeAiChat(restoreFocus) {
    if (!aiChat.open) return;
    aiChat.close();
    aiButton.setAttribute("aria-expanded", "false");
    if (restoreFocus) aiButton.focus();
  }
  aiButton.addEventListener("click", function () {
    if (aiChat.open) { closeAiChat(true); return; }
    aiChat.show();
    aiButton.setAttribute("aria-expanded", "true");
    if (!aiMessages.firstChild) appendAiMessage("Здравствуйте! Подскажу, как начать расчёт, открыть историю или выбрать парк на карте.", false);
    M.enter(aiChat, "fade");
    if (window.matchMedia("(pointer: fine)").matches) aiInput.focus();
    else $("#btnAiClose").focus();
  });
  $("#btnAiClose").addEventListener("click", function () { closeAiChat(true); });
  aiChat.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    e.preventDefault();
    e.stopPropagation();
    closeAiChat(true);
  });
  function appendAiMessage(text, fromUser) {
    var now = new Date(), day = now.toDateString();
    if (aiMessages.dataset.day !== day) {
      var divider = document.createElement("p");
      divider.className = "ai-chat__date";
      divider.textContent = aiDate.format(now);
      aiMessages.appendChild(divider);
      aiMessages.dataset.day = day;
    }
    var bubble = document.createElement("div");
    bubble.className = "ai-chat__message" + (fromUser ? " ai-chat__message--user" : "");
    var message = document.createElement("p"), meta = document.createElement("div"), time = document.createElement("time");
    message.textContent = text;
    meta.className = "ai-chat__meta";
    time.dateTime = now.toISOString();
    time.textContent = aiTime.format(now);
    meta.appendChild(time);
    if (fromUser) {
      var status = document.createElement("span");
      status.className = "ad-icon ad-icon--check";
      status.setAttribute("role", "img");
      status.setAttribute("aria-label", "Отправлено");
      status.title = "Отправлено";
      meta.appendChild(status);
    }
    bubble.append(message, meta);
    aiMessages.appendChild(bubble);
    aiMessages.scrollTop = aiMessages.scrollHeight;
  }
  function aiHelp(message) {
    if (/истори|прошл|журнал/.test(message)) return "Откройте «История» в верхнем меню и выберите рабочий день. События внутри расчёта собраны на вкладке «Журнал».";
    if (/карт|маршрут/.test(message)) return "На карте расчёта откройте список «Все парки» и выберите нужный парк. Нажмите на линию маршрута, чтобы открыть его карточку.";
    if (/расч[её]т|созда|новый день|подготов/.test(message)) return "Откройте «Создание» и нажмите «Создать расчёт». Выберите парк, проверьте дату, автобусы и водителей, затем нажмите «Рассчитать».";
    return "Пока я отвечаю на вопросы о работе в AUTODISP. Спросите, как открыть историю расчётов, создать рабочий день или выбрать парк на карте.";
  }
  aiInput.addEventListener("input", function () { aiSend.disabled = !aiInput.value.trim(); });
  aiInput.addEventListener("keydown", function (e) {
    if (e.key !== "Enter" || e.shiftKey || e.isComposing) return;
    e.preventDefault();
    if (!aiSend.disabled) aiForm.requestSubmit();
  });
  aiForm.addEventListener("submit", function (e) {
    e.preventDefault();
    var message = aiInput.value.trim();
    if (!message) return;
    appendAiMessage(message, true);
    appendAiMessage(aiHelp(message.toLocaleLowerCase("ru")), false);
    aiInput.value = "";
    aiSend.disabled = true;
    aiInput.focus();
  });

  // Профиль и настройки: локальная оболочка интерфейса, готовая для подключения
  // серверной авторизации и интеграционного API на следующем этапе.
  var profileButton = $("#btnProfile"), profileMenu = $("#profileMenu"), settingsDialog = $("#settingsDialog");
  function profileData() {
    var data = {}; try { data = JSON.parse(localStorage.getItem("autodisp.profile") || "{}") || {}; } catch (e) { data = {}; }
    return data;
  }
  function paintAvatar(el, image) {
    el.style.backgroundImage = image ? 'url("' + image + '")' : "";
    el.classList.toggle("has-image", !!image);
  }
  function renderProfile() {
    var p = profileData(), name = p.name || $("#loginName").value.trim() || "Профиль";
    $("#profileName").textContent = name;
    $("#profileMenuName").textContent = name;
    paintAvatar($("#profileAvatar"), p.avatar);
    paintAvatar($("#profileMenuAvatar"), p.avatar);
    paintAvatar($("#settingsAvatar"), p.avatar);
    $("#profileNameInput").value = name === "Профиль" ? "" : name;
  }
  function closeProfile() { profileMenu.hidden = true; profileButton.setAttribute("aria-expanded", "false"); }
  function openSettings() {
    closeProfile(); renderProfile(); settingsDialog.hidden = false; $("#settingsTitle").focus({ preventScroll: true });
  }
  function closeSettings() { settingsDialog.hidden = true; profileButton.focus({ preventScroll: true }); }
  profileButton.addEventListener("click", function () {
    var open = profileMenu.hidden; profileMenu.hidden = !open; profileButton.setAttribute("aria-expanded", String(open));
  });
  $("#btnSettings").addEventListener("click", openSettings);
  $("#btnSettingsClose").addEventListener("click", closeSettings);
  settingsDialog.addEventListener("click", function (e) { if (e.target.hasAttribute("data-settings-close")) closeSettings(); });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    if (!settingsDialog.hidden) closeSettings();
    else if (!profileMenu.hidden) { closeProfile(); profileButton.focus(); }
  });
  document.addEventListener("click", function (e) {
    if (!profileMenu.hidden && !profileMenu.contains(e.target) && e.target !== profileButton && !profileButton.contains(e.target)) closeProfile();
  });
  $("#avatarInput").addEventListener("change", function () {
    var file = this.files && this.files[0]; if (!file) return;
    var reader = new FileReader(); reader.onload = function () { var p = profileData(); p.avatar = reader.result; localStorage.setItem("autodisp.profile", JSON.stringify(p)); renderProfile(); }; reader.readAsDataURL(file);
  });
  $("#btnSettingsSave").addEventListener("click", function () {
    var p = profileData(), name = $("#profileNameInput").value.trim();
    if (!name) return toast("Введите имя профиля", true);
    p.name = name; try { localStorage.setItem("autodisp.profile", JSON.stringify(p)); } catch (e) { /* без памяти */ }
    renderProfile(); $("#settingsSaved").textContent = "Профиль сохранён"; toast("Профиль сохранён");
  });
  $("#passwordForm").addEventListener("submit", function (e) {
    e.preventDefault(); var a = $("#newPassword").value, b = $("#repeatPassword").value;
    if (a.length < 6) return toast("Новый пароль должен быть не короче 6 символов", true);
    if (a !== b) return toast("Пароли не совпадают", true);
    this.reset(); toast("Пароль обновлён для текущей демо-сессии");
  });
  $("#btnOneC").addEventListener("click", function () {
    $("#oneCStatus").textContent = "Готово к настройке"; this.textContent = "Настройка подключения"; toast("Экран подключения 1С подготовлен");
  });

  var dashboardNav = $("#dashboardNav");
  var workspaceScreens = { stats: "#scrStats", history: "#scrHistory", create: "#scrCreate" };
  var workspaceLinks = Array.from(dashboardNav.querySelectorAll("a"));
  $("#btnCompareStats").addEventListener("click", function () { toast("Шаблон сравнения готов для выбора периода"); });

  function today() { var d = new Date(); return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()); }
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function shortName(p) { return p.name.replace("Автобусный парк", "Парк"); }

  // ---------- экраны и адрес ----------
  var current = null;
  var welcome = $("#scrWelcome");
  var film = $("#welcomeFilm");
  var welcomeDraft = new URLSearchParams(location.search).get("hero") === "draft";
  var filmBase = welcomeDraft ? "img/welcome-bus" : "img/welcome-bus-preview";
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  function syncFilm() {
    if (current !== "welcome" || document.hidden || reduceMotion.matches) {
      film.pause();
      return;
    }
    if (!film.getAttribute("src")) film.src = filmBase + ".mp4";
    film.play().catch(function () {});
  }
  document.addEventListener("visibilitychange", syncFilm);
  reduceMotion.addEventListener("change", function () {
    if (reduceMotion.matches) {
      film.removeAttribute("src");
      film.load();
    }
    syncFilm();
  });
  var resultEditId = null;
  function go(name, silent, editId) {
    if (!signedIn && ["stats", "history", "create", "parks", "prep", "result"].indexOf(name) >= 0) name = "login";
    var changed = current !== name; current = name;
    if (changed && name === "parks") {
      P.sel = [];
      renderParks();
    }
    welcome.hidden = name !== "welcome";
    if (name === "welcome" && welcomeDraft) showWelcome();
    syncFilm();
    $("#scrLogin").hidden = name !== "login";
    resultEditId = name === "result" ? editId || null : null;
    Object.keys(workspaceScreens).forEach(function (page) { $(workspaceScreens[page]).hidden = name !== page; });
    $("#scrParks").hidden = name !== "parks"; $("#scrPrep").hidden = name !== "prep"; $("#scrResult").hidden = name !== "result";
    document.body.classList.toggle("is-welcome", name === "welcome");
    document.body.classList.toggle("is-dashboard", !!workspaceScreens[name]);
    dashboardNav.hidden = !workspaceScreens[name];
    workspaceLinks.forEach(function (link) {
      if (link.dataset.go === name) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
    aiButton.hidden = !signedIn || name === "welcome" || name === "login";
    $("#barProfile").hidden = aiButton.hidden;
    if (aiButton.hidden) {
      closeAiChat(false);
      aiMessages.replaceChildren();
      delete aiMessages.dataset.day;
      aiInput.value = "";
      aiSend.disabled = true;
      closeProfile();
    }
    if (changed && name !== "welcome") {
      var target = workspaceScreens[name] || { login: "#loginForm", parks: "#scrParks .scr__in", prep: "#scrPrep .scr__in", result: "#scrResult" }[name];
      M.enter($(target), "up");
    }
    document.body.classList.toggle("is-result", name === "result");
    renderCrumbs(name);
    if (!silent) {
      var hash = "#" + name;
      if (name === "welcome") hash = "#";
      else if (resultEditId) hash = "#edit/" + encodeURIComponent(resultEditId);
      else if (name === "prep" || name === "result") hash += "/" + key();
      if (location.hash !== hash) history.pushState(null, "", hash);
    }
    if (name === "result") window.App.resize();
    var scr = $(workspaceScreens[name] || (name === "parks" ? "#scrParks" : name === "prep" ? "#scrPrep" : "#scrResult"));
    if (scr && name !== "prep") scr.scrollTop = 0;
    if (changed && name !== "welcome" && name !== "result") {
      var heading = name === "login" ? $("#loginName") : $("h1", scr);
      if (heading) {
        if (heading.tagName === "H1") heading.tabIndex = -1;
        heading.focus({ preventScroll: true });
      }
    }
  }
  // Крошки показывают путь от истории рабочих дней к расчёту.
  function renderCrumbs(name) {
    var no = window.App && window.App.runNo && window.App.runNo();
    if (name === "welcome" || name === "login" || workspaceScreens[name]) { $("#crumbs").innerHTML = ""; return; }
    var crumbs = [['<button data-go="create">Создание</button>', false]];
    if (!resultEditId) crumbs.push(['<button data-go="parks">Выбор парков</button>', name === "parks"]);
    if (!resultEditId && (name === "prep" || name === "result") && P.ids.length) crumbs.push(['<button data-go="prep">Подготовка дня</button>', name === "prep"]);
    if (name === "result") crumbs.push(["<span>" + (resultEditId ? "Редактирование расчёта" : "Расчёт" + (no ? " № " + no : "")) + "</span>", true]);
    $("#crumbs").innerHTML = crumbs.map(function (c) { return '<span class="crumb' + (c[1] ? ' is-on" aria-current="page' : "") + '">' + c[0] + "</span>"; }).join('<span class="crumb__sep" aria-hidden="true">/</span>');
  }
  // Логотип возвращает к рабочим дням после входа.
  $("#btnHome").addEventListener("click", function () {
    var to = signedIn ? "stats" : "welcome";
    if (to === current) return;
    confirmLeave().then(function (ok) { if (!ok) return; openScreen(to); });
  });
  window.addEventListener("autodisp:run", function () { if (current === "result") renderCrumbs("result"); });
  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-go]"); if (!b) return;
    var to = b.getAttribute("data-go");
    if (["stats", "history", "create", "parks", "prep", "result", "welcome"].indexOf(to) < 0) return;
    if (b.tagName === "A") {
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
      e.preventDefault();
    }
    if (to === "result" && !P.calculated) return;
    if (to === current) return;
    confirmLeave().then(function (ok) { if (ok) { openScreen(to); } });
  });
  window.addEventListener("popstate", function () {
    if (current === "prep" && pending().length && location.hash.indexOf("#prep/") !== 0) {
      var back = location.hash;
      history.pushState(null, "", "#prep/" + key());  // остаёмся, пока не ответит
      confirmLeave().then(function (ok) { if (ok) { history.replaceState(null, "", back || location.pathname); route(); } });
      return;
    }
    route();
  });
  // Адрес экрана: парки через запятую, а у реестра из файла - «file». После перезагрузки файла в памяти нет,
  // и «file» ведёт на выбор парков, а не подменяет загруженное реестром парка с сервера.
  function key() { return P.file ? "file" : P.ids.join(","); }
  function route() {
    if (!signedIn) return go("login", true);
    var h = location.hash.slice(1).split("/");
    if ((h[0] === "prep" || h[0] === "result") && h[1]) {
      if (key() === h[1] && P.base) return go(h[0] === "result" && P.calculated ? "result" : "prep", true);
      return openParks(h[1].split(","), true);
    }
    if (h[0] === "edit" && /^day-[0-9]+$/.test(h[1])) {
      if (current === "result" && resultEditId === h[1]) return;
      go("create", true);
      setSavedRunsOpen(true);
      return editSavedDay(h[1], true);
    }
    var name = h[0] === "parks" || workspaceScreens[h[0]] ? h[0] : "stats";
    openScreen(name, true);
  }
  // Каждое открытие приложения начинается с приветствия, даже если в адресе сохранён экран.
  function firstRoute() {
    history.replaceState(null, "", location.pathname + location.search);
    go("welcome", true);
  }

  // ---------- 0. Приветствие ----------
  function hello() {
    var h = new Date().getHours();
    return h >= 5 && h < 12 ? "Доброе утро" : h >= 12 && h < 17 ? "Добрый день" : h >= 17 && h < 23 ? "Добрый вечер" : "Доброй ночи";
  }
  function showWelcome() {
    $("#welcomeHello").textContent = "Помогаем вашему нарядчику";
  }
  welcome.classList.add("welcome--film");
  film.poster = filmBase + ".jpg";
  film.hidden = false;
  if (welcomeDraft) {
    welcome.classList.add("welcome--draft");
    $("#welcomeIntro").hidden = true;
  }
  // После приветствия открывается форма входа.
  $("#btnStart").addEventListener("click", function () {
    go("login");
  });

  $("#loginForm").addEventListener("submit", function (e) {
    e.preventDefault();
    if (!$("#loginName").value.trim() || !$("#loginPassword").value) return;
    signedIn = true;
    $("#loginPassword").value = "";
    renderProfile();
    if (/^#(?:prep\/|edit\/|stats$|history$|create$)/.test(entryHash)) {
      history.replaceState(null, "", entryHash);
      entryHash = "";
      route();
    } else openScreen("stats");
  });
  $("#btnProfileLogout").addEventListener("click", function () {
    closeProfile();
    profileButton.focus();
    confirmLeave().then(function (ok) {
      if (!ok) return;
      signedIn = false; P.calculated = false;
      $("#loginName").value = ""; $("#loginPassword").value = "";
      go("welcome");
    });
  });
  $("#btnNewDay").addEventListener("click", function () { go("parks"); });

  var dashboardDays = [], dashboardSelected = null;
  function dateLabel(s) {
    var d = new Date(s + "T12:00:00");
    return isNaN(d.getTime()) ? s : new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long", year: "numeric" }).format(d);
  }
  function stat(value, label) { return '<div class="dashboard__stat"><b>' + value + '</b><span>' + label + "</span></div>"; }
  function openScreen(name, silent) {
    go(name, silent);
    if (name === "stats" || name === "history" || (name === "create" && !$("#savedRuns").hidden)) loadDays(name);
  }
  function loadDays(page) {
    if (page === "history") $("#dashboardDays").innerHTML = '<p class="muted">Загружаем историю…</p>';
    if (page === "create") $("#savedRunsStatus").textContent = "Загружаем расчёты…";
    return D.api("GET", "/api/days").then(function (r) {
      if (current !== page) return;
      dashboardDays = (r.days || []).slice().sort(function (a, b) { return b.date.localeCompare(a.date) || b.day_id.localeCompare(a.day_id, undefined, { numeric: true }); });
      var done = dashboardDays.filter(function (d) { return d.planned; });
      if (page === "stats") {
        $("#dashboardStats").innerHTML = stat(done.length, "дней с расчётом") + stat(done.reduce(function (n, d) { return n + d.events; }, 0), "событий") + stat(done.reduce(function (n, d) { return n + d.edits; }, 0), "ручных правок");
      } else if (page === "history") {
        $("#dashboardDays").innerHTML = dashboardDays.length ? dashboardDays.map(function (d) {
          return '<button class="dashboard__day" type="button" data-day="' + esc(d.day_id) + '"><b>' + esc(dateLabel(d.date)) + '</b><span>' + (d.planned ? "Расчёт выполнен" : "Без расчёта") + ' · событий: ' + d.events + ' · правок: ' + d.edits + "</span></button>";
        }).join("") : '<p class="muted">Сохранённых дней пока нет.</p>';
        if (dashboardDays.length) selectDashboardDay(dashboardDays.some(function (d) { return d.day_id === dashboardSelected; }) ? dashboardSelected : dashboardDays[0].day_id);
        else $("#dashboardDetail").innerHTML = '<h2>День</h2><p class="muted">После первого расчёта здесь появится история.</p>';
      } else {
        $("#savedRunsStatus").textContent = done.length ? "" : "Сохранённых расчётов пока нет.";
        $("#savedRunsList").innerHTML = done.map(function (d) {
          return '<button class="dashboard__day" type="button" data-edit-day="' + esc(d.day_id) + '"><b>' + esc(dateLabel(d.date)) + '</b><span>' +
            esc((d.parks || []).join(", ") || "Сохранённый расчёт") + ' · правок: ' + d.edits + '</span></button>';
        }).join("");
      }
    }, function (e) {
      if (current !== page) return;
      var target = { stats: "#dashboardStats", history: "#dashboardDays", create: "#savedRunsStatus" }[page];
      $(target).textContent = "Не удалось загрузить данные. " + e.message;
    });
  }
  function setSavedRunsOpen(open) {
    $("#btnEditDay").setAttribute("aria-expanded", String(open));
    if (open) M.show($("#savedRuns"), "down");
    else M.hide($("#savedRuns"), "down");
  }
  var editRequest = 0;
  $("#btnEditDay").addEventListener("click", function () {
    var open = this.getAttribute("aria-expanded") !== "true";
    setSavedRunsOpen(open);
    if (open) loadDays("create");
    else editRequest++;
  });
  $("#savedRunsList").addEventListener("click", function (e) {
    var button = e.target.closest("[data-edit-day]");
    if (button) editSavedDay(button.dataset.editDay);
  });
  function editSavedDay(id, silent) {
    var request = ++editRequest;
    var hash = location.hash;
    function stillHere() { return current === "create" && request === editRequest && location.hash === hash; }
    $("#savedRunsStatus").textContent = "Открываем расчёт…";
    return window.App.openSaved(id, function () {
      if (!stillHere()) return false;
      $("#savedRunsStatus").textContent = "";
      P.calculated = false;
      go("result", silent, id);
    }).catch(function (e) {
      if (stillHere()) $("#savedRunsStatus").textContent = "Не удалось открыть расчёт. " + e.message;
    });
  }
  $("#dashboardDays").addEventListener("click", function (e) {
    var b = e.target.closest("[data-day]"); if (b) selectDashboardDay(b.getAttribute("data-day"));
  });
  function selectDashboardDay(id) {
    var entry = dashboardDays.filter(function (d) { return d.day_id === id; })[0];
    if (!entry) return;
    dashboardSelected = id;
    $("#dashboardDays").querySelectorAll("[data-day]").forEach(function (b) { b.classList.toggle("is-on", b.getAttribute("data-day") === id); });
    var box = $("#dashboardDetail");
    box.innerHTML = '<h2>' + esc(dateLabel(entry.date)) + '</h2><p class="muted">Загружаю данные дня…</p>';
    var base = "/api/days/" + encodeURIComponent(id);
    Promise.all([D.api("GET", base), entry.planned ? D.api("GET", base + "/state") : Promise.resolve(null)]).then(function (r) {
      if (current !== "history" || dashboardSelected !== id) return;
      var day = r[0], state = r[1], logs = state ? state.log || [] : [];
      var parks = (day.parks || []).map(function (p) { return p.name; });
      var parkIds = (day.parks || []).map(function (p) { return p.id; });
      return D.api("GET", "/x/marks/journal?date=" + encodeURIComponent(entry.date) + "&parks=" + parkIds.map(encodeURIComponent).join(",")).then(function (marks) {
        return { day: day, state: state, logs: logs, parks: parks, marks: marks.journal || [] };
      }, function () { return { day: day, state: state, logs: logs, parks: parks, marks: [] }; });
    }).then(function (data) {
      if (!data || current !== "history" || dashboardSelected !== id) return;
      var day = data.day, state = data.state, logs = data.logs, parks = data.parks;
      var duties = (day.duties || []).filter(function (d) { return d.type === "line"; }).length;
      var shifts = (day.shifts || []).length;
      var names = {};
      (day.drivers || []).forEach(function (d) { names[d.id] = "Водитель " + (d.full_name || d.tab_number || d.id); });
      (day.vehicles || []).forEach(function (v) { names[v.id] = "Автобус " + (v.number || v.garage_number || v.id); });
      (day.routes || []).forEach(function (r) { names[r.id] = "Маршрут " + (r.number || r.id); });
      var logHtml = logs.slice().reverse().map(function (l) {
        var title = l.title || l.type || "Событие";
        var time = l.at || l.from || "";
        return '<li class="dashboard__log"><time>' + esc(time) + '</time><span><b>' + esc(title) + '</b>' + (l.reason ? '<small>' + esc(l.reason) + "</small>" : "") + "</span></li>";
      }).join("");
      logHtml += data.marks.map(function (m) {
        var label = names[m.id] || m.id;
        var stateText = m.kind === "route" ? (m.value ? "работает" : "не работает") : m.kind === "veh" ? (m.value ? "исправен" : "неисправен") : (m.value ? "выходит" : "не выходит");
        var time = new Date(m.at);
        return '<li class="dashboard__log"><time>' + esc(isNaN(time.getTime()) ? m.at : new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit" }).format(time)) + '</time><span><b>' + esc(label) + '</b><small>' + esc(stateText) + "</small></span></li>";
      }).join("");
      if (!logHtml) logHtml = '<li class="muted">Записей пока нет.</li>';
      box.innerHTML = '<h2>' + esc(dateLabel(entry.date)) + '</h2><p class="muted">' + esc(parks.join(", ") || "Парк не указан") + '</p><div class="dashboard__facts">' + stat(duties, "нарядов на линии") + stat(shifts, "смен") + stat(state ? state.unfilled.length : "—", "незакрытых назначений") + '</div><h3>Журнал дня</h3><ol class="dashboard__logs">' + logHtml + "</ol>";
    }, function (e) { if (current === "history" && dashboardSelected === id) box.innerHTML = '<h2>' + esc(dateLabel(entry.date)) + '</h2><p class="err">Не удалось открыть день: ' + esc(e.message) + "</p>"; });
  }

  // ---------- 1. Выбор парков ----------
  // Клик по карточке отмечает парк; можно выбрать несколько и рассчитать их одним днём.
  function parkById(id) { return P.parks.filter(function (p) { return p.id === id; })[0] || (P.file && P.fileParks[id]) || undefined; }
  function selTitle(short) {
    var ps = P.ids.map(parkById).filter(Boolean);
    if (ps.length === 1) return short ? shortName(ps[0]) : ps[0].name;
    return (short ? "Парки " : "Автобусные парки ") + ps.map(function (p) { return p.name.replace(/^.*№\s*/, "№"); }).join(", ");
  }
  // Виды транспорта: у каждого свой раскрывающийся список парков. Пока реестры есть только у автобусных.
  var KINDS = [
    { id: "bus", name: "Автобусы", icon: '<i class="ad-icon ad-icon--bus"></i>' },
    { id: "trolley", name: "Троллейбусы", icon: '<i class="tr-ic tr-ic--trolley"></i>' },
    { id: "tram", name: "Трамваи", icon: '<i class="tr-ic tr-ic--tram"></i>' }
  ];
  function num(n) { return n == null ? "?" : D.num(n); }
  function parkCard(p) {
    var on = p.available, tag = on ? "button" : "div", picked = P.sel.indexOf(p.id) >= 0;
    var vals = [[on ? p.routes : null, "маршрутов"], [on ? p.vehicles : null, "автобусов"], [on ? p.release_weekday : null, "выпуск в будни"], [on ? p.release_weekend : null, "выпуск в выходные"]];
    return "<" + tag + ' class="pc' + (on ? "" : " pc--locked") + (picked ? " is-picked" : "") + '"' +
      (on ? ' type="button" role="checkbox" aria-checked="' + picked + '" data-park="' + p.id + '"' : ' aria-disabled="true"') + ">" +
      '<span class="pc__top"><span class="pc__ic"><i class="ad-icon ad-icon--bus"></i></span>' + (on ? '<span class="pc__check" aria-hidden="true"></span>' : "") + "</span>" +
      '<span class="pc__name">' + esc(p.name) + '</span><span class="pc__addr">' + esc(p.address || "") + "</span>" +
      '<span class="pc__nums">' + vals.map(function (x) { return "<span><b>" + num(x[0]) + "</b>" + x[1] + "</span>"; }).join("") + "</span>" +
      (on ? "" : '<span class="pc__go pc__go--off">Реестр парка пока не подключён</span>') + "</" + tag + ">";
  }
  function renderParks() {
    $("#parks").innerHTML = KINDS.map(function (k) {
      var list = P.parks.filter(function (p) { return (p.kind || "bus") === k.id; });
      var avail = list.filter(function (p) { return p.available; }).length;
      var head = '<span class="kind__ic">' + k.icon + '</span><span class="kind__t">' + k.name + "</span>";
      // Вид транспорта без парков - недоступен: полупрозрачный, не раскрывается
      if (!list.length) return '<div class="kind kind--off" aria-disabled="true"><div class="kind__head">' + head + '<span class="kind__n">парки не подключены</span></div></div>';
      var count = list.length + " " + D.plural(list.length, "парк", "парка", "парков") + (avail < list.length ? ", подключено " + avail : "");
      return '<details class="kind" data-kind="' + k.id + '"><summary class="kind__head">' + head +
        '<span class="kind__n">' + count + '</span><i class="ad-icon ad-icon--caret-down ad-icon--sm kind__car"></i></summary>' +
        '<div class="parks__grid">' + list.map(parkCard).join("") + "</div></details>";
    }).join("");
    $("#parks").querySelectorAll("[data-park]").forEach(function (b) {
      b.addEventListener("click", function () {
        var id = b.getAttribute("data-park"), i = P.sel.indexOf(id);
        if (i >= 0) P.sel.splice(i, 1); else P.sel.push(id);
        // карточку не перерисовываем - иначе она пересоздаётся под курсором и рамка «прыгает»
        paintParkPick(b, i < 0);
        updatePick();
      });
    });
    updatePick();
  }
  function paintParkPick(button, picked) {
    button.classList.toggle("is-picked", picked);
    button.setAttribute("aria-checked", String(picked));
  }
  function updatePick() {
    var ps = P.sel.map(parkById).filter(Boolean), avail = P.parks.filter(function (p) { return p.available; });
    var routes = 0, veh = 0; ps.forEach(function (p) { routes += p.routes; veh += p.vehicles; });
    $("#pickText").innerHTML = ps.length
      ? '<span class="go__m">Выбрано: <b>' + ps.length + "</b> " + D.plural(ps.length, "парк", "парка", "парков") + "</span>" +
        '<span class="go__m"><i class="ad-icon ad-icon--path ad-icon--sm"></i><b>' + routes + "</b> " + D.plural(routes, "маршрут", "маршрута", "маршрутов") + "</span>" +
        '<span class="go__m"><i class="ad-icon ad-icon--bus ad-icon--sm"></i><b>' + veh + "</b> " + D.plural(veh, "автобус", "автобуса", "автобусов") + "</span>"
      : '<span class="go__m">Выберите один или несколько парков</span>';
    $("#btnPickAll").hidden = ps.length === avail.length;
    $("#btnWork").disabled = !ps.length;
  }
  $("#btnPickAll").addEventListener("click", function () {
    P.sel = P.parks.filter(function (p) { return p.available; }).map(function (p) { return p.id; });
    $("#parks").querySelectorAll("[data-park]").forEach(function (b) { paintParkPick(b, true); });
    updatePick();
  });

  $("#btnUpload").addEventListener("click", function () { D.upload.open(openFile); });
  $("#btnWork").addEventListener("click", function () {
    if (!P.sel.length) return;
    // порядок - как на экране
    openParks(P.parks.map(function (p) { return p.id; }).filter(function (id) { return P.sel.indexOf(id) >= 0; }));
  });

  // ---------- 2. Подготовка ----------
  // Реестры выбранных парков складываются в один день: движок сам решит переброски между ними.
  function openParks(ids, silent) {
    P.file = null;
    ids = ids.filter(function (id) { var p = parkById(id); return p && p.available; });
    if (!ids.length) return go("parks", silent);
    begin(ids, today(), silent);
    Promise.all(ids.map(function (id) { return D.loadRegistry(id, P.date); }).concat([D.loadGeo(), D.loadMarks(ids, P.date)])).then(function (r) {
      ready(merge(r.slice(0, ids.length)), r[ids.length + 1]);
    }, failed);
  }
  // Реестр, загруженный файлом (окно «Загрузить реестр»): тот же экран подготовки, данные - из файла.
  // Парки файла могут не совпадать со списком главного экрана, поэтому их названия берутся из файла.
  function openFile(day, name) {
    P.file = name; P.fileParks = {};
    day.parks.forEach(function (p) { P.fileParks[p.id] = p; });
    begin(day.parks.map(function (p) { return p.id; }), day.meta.date, false);
    Promise.all([D.loadGeo(), D.loadMarks(P.ids, P.date)]).then(function (r) { ready(day, r[1]); }, failed);
  }
  function begin(ids, date, silent) {
    P.ids = ids; P.date = date; P.calculated = false; P.base = null; P.tab = null; P.open = {};
    $("#prepTitle").innerHTML = esc(selTitle(false)) + (P.file ? ' <span class="ad-badge ad-badge--neutral prep-src" title="' + esc(P.file) + '">из файла</span>' : "");
    $("#btnGeo").href = "/routes#park=" + ids[0];
    go("prep", silent);
    $("#ready").innerHTML = ""; $("#sections").innerHTML = '<div class="lp__load"><span class="spinner"></span> Загружаю ' + (ids.length > 1 ? "реестры парков" : "реестр парка") + "…</div>";
    $("#sections").inert = false;
    $("#sections").firstElementChild.setAttribute("role", "status");
  }
  function ready(base, saved) {
    P.base = base;
    P.vehById = Object.fromEntries(base.vehicles.map(function (v) { return [v.id, v]; }));
    P.columns = Ops.groupRoutes(base.routes);
    P.st = initState();
    // отметки, сделанные в этот день раньше, - поверх реестра
    ["route", "veh", "drv"].forEach(function (k) { Object.keys(saved[k] || {}).forEach(function (id) { if (id in P.st[k]) P.st[k][id] = saved[k][id]; }); });
    P.saved = clone(P.st);
    updateSave();
    $("#sections").innerHTML = "";
    refresh(); renderPanel();
  }
  function failed(e) { $("#sections").innerHTML = '<p class="err" role="alert">' + esc(e.message) + "</p>"; }
  function merge(days) {
    var out = JSON.parse(JSON.stringify(days[0]));
    days.slice(1).forEach(function (d) {
      ["parks", "routes", "duties", "shifts", "vehicles", "drivers"].forEach(function (k) { out[k] = out[k].concat(d[k]); });
    });
    return out;
  }

  // Состояние дня: что работает, какие автобусы исправны, какие водители выходят.
  // Начальные значения - из реестра, поверх - отметки диспетчера за этот день из базы (naryad/web/marks.py).
  function initState() {
    var st = { route: {}, veh: {}, drv: {} };
    P.base.routes.forEach(function (r) { st.route[r.id] = true; });
    P.base.vehicles.forEach(function (v) { st.veh[v.id] = v.condition === "ok"; });
    // выходит = работает по графику и прошёл утренний медосмотр (правило движка may_depart)
    P.base.drivers.forEach(function (d) { st.drv[d.id] = d.schedule === "work" && d.medical === "passed"; });
    return st;
  }
  // Переключения копятся, пока не нажата «Сохранить» (или «Рассчитать» - она сохраняет сама).
  // Несохранённое = отличия текущего состояния от последнего сохранённого.
  function clone(o) { return JSON.parse(JSON.stringify(o)); }
  function mark(kind, ids, value) { ids.forEach(function (id) { P.st[kind][id] = value; }); updateSave(); }
  function pending() {
    var out = [];
    if (!P.st || !P.saved) return out;
    ["route", "veh", "drv"].forEach(function (k) {
      Object.keys(P.st[k]).forEach(function (id) { if (P.st[k][id] !== P.saved[k][id]) out.push({ kind: k, id: id, value: P.st[k][id] }); });
    });
    return out;
  }
  function updateSave() {
    var n = pending().length, b = $("#btnSave");
    b.disabled = !n;
    b.querySelector("span").textContent = n ? "Сохранить (" + n + ")" : "Сохранено";
    b.classList.toggle("is-dirty", !!n);
  }
  function save() {
    var items = pending();
    if (!items.length) return Promise.resolve();
    // парк каждой отметки - из реестра: в коде из файла перевозчика («М11», «7001») его нет
    var park = {};
    [["route", P.base.routes], ["veh", P.base.vehicles], ["drv", P.base.drivers]].forEach(function (x) {
      x[1].forEach(function (r) { park[x[0] + ":" + r.id] = r.park_id; });
    });
    items.forEach(function (it) { it.park = park[it.kind + ":" + it.id]; });
    $("#btnSave").disabled = true;
    return D.saveMarks(P.date, items).then(function () {
      items.forEach(function (it) { P.saved[it.kind][it.id] = it.value; });
      updateSave(); toast("Сохранено: " + items.length + " " + D.plural(items.length, "изменение", "изменения", "изменений"));
    }, function (e) { updateSave(); toast("Не сохранилось: " + e.message, true); throw e; });
  }
  $("#btnSave").addEventListener("click", function () { save().catch(function () {}); });
  // Уход с подготовки с несохранённым - спросить; согласие сбрасывает несохранённое
  function confirmLeave() {
    var n = pending().length;
    if (!n || current !== "prep") return Promise.resolve(true);
    return D.events.confirm({ title: "Изменения не сохранены",
      text: "Несохранённых изменений: " + n + ". Если уйти, они пропадут.", ok: "Уйти без сохранения", cancel: "Остаться", danger: true })
      .then(function (ok) { if (ok) { P.st = clone(P.saved); updateSave(); } return ok; });
  }
  window.addEventListener("beforeunload", function (e) { if (current === "prep" && pending().length) { e.preventDefault(); e.returnValue = ""; } });
  function routeOn(r) { return P.st.route[r.id]; }
  function vehOk(v) { return P.st.veh[v.id]; }
  function drvGoes(d) { return P.st.drv[d.id]; }

  // Сводка готовности: что сегодня работает и хватает ли
  function counts() {
    var b = P.base, on = {}, need = {}, have = {}, shifts = 0, lineDuties = 0, res = 0;
    b.routes.forEach(function (r) { on[r.id] = routeOn(r); });
    var dutyOn = {};
    b.duties.forEach(function (d) {
      if (d.type === "line" && !on[d.route_id]) return;
      dutyOn[d.id] = 1; need[d.vehicle_class] = (need[d.vehicle_class] || 0) + 1;
      if (d.type === "line") lineDuties++; else res++;
    });
    b.shifts.forEach(function (s) { if (dutyOn[s.duty_id]) shifts++; });
    var vehOk = 0;
    b.vehicles.forEach(function (v) { if (P.st.veh[v.id]) { vehOk++; have[v.class] = (have[v.class] || 0) + 1; } });
    var drvReady = 0;
    b.drivers.forEach(function (d) { if (P.st.drv[d.id]) drvReady++; });
    var lack = Object.keys(need).map(function (c) { return [c, need[c] - (have[c] || 0)]; }).filter(function (x) { return x[1] > 0; });
    return { routes: b.routes.filter(routeOn).length, lineDuties: lineDuties, res: res, shifts: shifts, vehOk: vehOk, vehAll: b.vehicles.length,
      drvReady: drvReady, drvAll: b.drivers.length, lack: lack, drvLack: Math.max(0, shifts - drvReady) };
  }

  // Три карточки: маршруты, автобусы, водители. Карточка открывает список под собой.
  var CARDS = [["routes", "path", "Колонны"], ["veh", "bus", "Автобусы"], ["drv", "users", "Водители"]];
  function refresh() {
    if (!P.base) return;
    var c = counts();
    // Карточка: заголовок и подписанные показатели. Красным - то, чего не хватает.
    var lackText = c.lack.map(function (x) { return x[1] + " " + D.CLASSES[x[0]]; }).join(", ");
    var stats = {
      routes: [["колонн", P.columns.filter(function (g) { return g.demo; }).length], ["маршрутов в работе", c.routes + " из " + P.base.routes.length], ["нарядов на линии", c.lineDuties]],
      veh: [["исправны", c.vehOk + " из " + c.vehAll], ["нужно на наряды", c.lineDuties + c.res], ["не хватает", c.lack.length ? lackText : 0, c.lack.length ? "bad" : ""]],
      drv: [["выходят", c.drvReady + " из " + c.drvAll], ["нужно на смены", c.shifts], ["не хватает", c.drvLack, c.drvLack ? "bad" : ""]]
    };
    if (!$("#ready").childElementCount) {
      $("#ready").innerHTML = CARDS.map(function (k) {
        return '<button type="button" class="rc-card" data-card="' + k[0] + '" aria-expanded="false" aria-controls="sections"><span class="rc-card__head"><span class="rc-card__ic"><i class="ad-icon ad-icon--' + k[1] + '"></i></span>' +
          '<span class="rc-card__l">' + k[2] + '</span><i class="ad-icon ad-icon--caret-down ad-icon--sm rc-card__car"></i></span><span class="rc-card__stats"></span></button>';
      }).join("");
      M.stagger($("#ready").children);
      $("#ready").querySelectorAll("[data-card]").forEach(function (b) {
        b.addEventListener("click", function () { var k = b.getAttribute("data-card"); P.tab = P.tab === k ? null : k; renderPanel(); });
      });
    }
    CARDS.forEach(function (k) {
      var el = $('[data-card="' + k[0] + '"]'), bad = stats[k[0]].some(function (x) { return x[2] === "bad"; });
      el.className = "rc-card" + (bad ? " rc-card--bad" : "") + (P.tab === k[0] ? " is-on" : "");
      el.setAttribute("aria-expanded", String(P.tab === k[0]));
      el.querySelector(".rc-card__stats").innerHTML = stats[k[0]].map(function (x) {
        return '<span class="rc-st' + (x[2] ? " rc-st--" + x[2] : "") + '"><b>' + esc(typeof x[1] === "number" ? D.num(x[1]) : x[1]) + "</b><span>" + esc(x[0]) + "</span></span>";
      }).join("");
    });
    $("#goText").innerHTML =
      '<span class="go__m"><i class="ad-icon ad-icon--path ad-icon--sm"></i><b>' + c.routes + "</b> " + D.plural(c.routes, "маршрут", "маршрута", "маршрутов") + " · <b>" + c.lineDuties + "</b> " + D.plural(c.lineDuties, "наряд", "наряда", "нарядов") + "</span>" +
      '<span class="go__m"><i class="ad-icon ad-icon--bus ad-icon--sm"></i><b>' + c.vehOk + "</b> " + D.plural(c.vehOk, "автобус", "автобуса", "автобусов") + "</span>" +
      '<span class="go__m"><i class="ad-icon ad-icon--users ad-icon--sm"></i><b>' + c.drvReady + "</b> " + D.plural(c.drvReady, "водитель", "водителя", "водителей") + "</span>";
    document.querySelectorAll("[data-group]").forEach(updateGroupHead);
  }

  // Список выбранной карточки - одно представление на раздел.
  function renderPanel() {
    var el = $("#sections"), wasOpen = el.classList.contains("is-open");
    refresh();
    el.inert = !P.tab;
    if (!P.tab) {
      // закрытие: панель уходит вверх и гаснет, потом очищается
      if (!wasOpen) { el.innerHTML = ""; el.className = "list-panel"; return; }
      var a = el.animate(M.reduce ? [{ opacity: 1 }, { opacity: 0 }] : [{ opacity: 1, transform: "none" }, { opacity: 0, transform: "translateY(-6px)" }], { duration: M.FAST, easing: M.EASE_EXIT, fill: "forwards" });
      M.after(a, M.FAST, function () { if (!P.tab) { el.innerHTML = ""; el.className = "list-panel"; } a.cancel(); });
      return;
    }
    el.className = "list-panel is-open";
    el.innerHTML = '<div class="lp__body"></div>';
    if (wasOpen) M.swap(el.firstChild); else M.enter(el, "down");
    var body = el.querySelector(".lp__body");
    if (P.tab === "routes") routesBody(body); else if (P.tab === "veh") vehBody(body); else drvBody(body);
    refresh();
  }

  function routeInfo(r) {
    var g = D.geoFor(r.park_id, r.number);
    var dutiesToday = P.base.duties.filter(function (d) { return d.route_id === r.id; });
    return { g: g, duties: dutiesToday.length, shifts: P.base.shifts.filter(function (s) { return dutiesToday.some(function (d) { return d.id === s.duty_id; }); }).length };
  }
  function chip(r, g) { return '<span class="num" style="background:' + D.routeBadgeColor(g && g.color) + '">' + esc(r.number) + "</span>"; }

  // Колонна содержит несколько маршрутов. Отметка готовности остаётся у маршрута.
  function routesBody(el) {
    var help = P.columns.some(function (g) { return g.demo; })
      ? "Демонстрационное закрепление по направлениям парка № 7." : "Для этих маршрутов закрепление за колоннами не задано.";
    var head = '<div class="ad-table-wrap"><table class="ad-table tbl tbl--routes"><thead><tr><th>В работе</th>' +
      '<th>Маршрут</th><th>Конечные</th><th>Важность</th><th class="ad-num">Нарядов сегодня</th><th class="ad-num">Смен</th><th>Класс</th></tr></thead><tbody>';
    el.innerHTML = '<div class="column-tools"><p class="muted">' + help + '</p><label class="sw sw--text"><input type="checkbox" data-all-routes aria-label="Все маршруты"><span></span><em>Все</em></label></div>' +
      '<div class="column-groups">' + P.columns.map(function (g, index) {
        var title = g.name + (P.ids.length > 1 ? " · " + shortName(parkById(g.park)) : "");
        return '<details class="column-group" data-column="' + index + '"' + (P.open["column:" + g.id] ? ' open' : '') +
          '><summary><span>' + esc(title) + '</span><span class="column-group__meta"></span></summary>' + head +
          g.routes.map(function (r) {
            var i = routeInfo(r), on = routeOn(r);
            return '<tr class="' + (on ? "" : "is-off") + '"><td><label class="sw"><input type="checkbox" data-route="' + esc(r.id) +
              '"' + (on ? " checked" : "") + ' aria-label="Маршрут ' + esc(r.number) + ' в работе"><span></span></label></td><td>' +
              Ops.recordButton("route", r.id, r.park_id, r.number) + "</td><td>" + esc(i.g ? i.g.name : "—") +
              "</td><td>" + D.PRIORITY[r.priority] + '</td><td class="ad-num">' + i.duties + '</td><td class="ad-num">' + i.shifts +
              "</td><td>" + r.allowed_classes.map(function (c) { return D.CLASSES[c]; }).join(", ") + "</td></tr>";
          }).join("") + '</tbody></table></div><details class="column-schedule"><summary>Распорядок выпуска</summary><div data-column-schedule></div></details></details>';
      }).join("") + '</div>';
    var all = el.querySelector("[data-all-routes]");
    var groups = Array.from(el.querySelectorAll("[data-column]")).map(function (box) {
      var group = P.columns[Number(box.dataset.column)], ids = new Set(group.routes.map(function (r) { return r.id; }));
      var duties = P.base.duties.filter(function (d) { return ids.has(d.route_id); });
      var fleet = P.base.vehicles.filter(function (v) { return ids.has(v.home_route_id); });
      var drivers = P.base.drivers.filter(function (d) {
        var v = P.vehById[d.home_vehicle_id]; return v && ids.has(v.home_route_id);
      });
      box.addEventListener("toggle", function () { if (box.isConnected) P.open["column:" + group.id] = box.open; });
      var schedule = box.querySelector(".column-schedule");
      var data = { box: box, group: group, duties: duties, fleet: fleet, drivers: drivers, schedule: schedule };
      schedule.addEventListener("toggle", function () { if (schedule.open) paintSchedule(data); });
      return data;
    });
    function paintSchedule(data) {
      var slots = data.duties.filter(function (d) { return P.st.route[d.route_id]; }).sort(function (a, b) {
        return a.start.localeCompare(b.start) || a.id.localeCompare(b.id);
      });
      var box = data.schedule.querySelector("[data-column-schedule]");
      if (!slots.length) { box.innerHTML = '<p class="muted">Нет нарядов на включённых маршрутах.</p>'; return; }
      var byId = new Map(data.group.routes.map(function (r) { return [r.id, r]; }));
      box.innerHTML = '<div class="ad-table-wrap"><table class="ad-table tbl"><thead><tr><th>На линии</th><th>Маршрут</th><th>Наряд</th><th>Класс</th><th>Смен</th></tr></thead><tbody>' +
        slots.map(function (d) {
          var r = byId.get(d.route_id);
          return "<tr><td>" + esc(d.start) + "–" + esc(d.end) + "</td><td>" +
            Ops.recordButton("route", r.id, r.park_id, r.number) + "</td><td>" + esc(d.id) +
            "</td><td>" + esc(D.CLASSES[d.vehicle_class] || d.vehicle_class) + "</td><td>" + d.shift_count + "</td></tr>";
        }).join("") + "</tbody></table></div>";
    }
    function syncAll() {
      var ids = Object.keys(P.st.route), on = ids.filter(function (k) { return P.st.route[k]; }).length;
      all.checked = ids.length > 0 && on === ids.length;
      all.disabled = !ids.length;
      all.indeterminate = on > 0 && on < ids.length;
      all.parentNode.querySelector("em").textContent = on === ids.length ? "Все" : on ? on + " из " + ids.length : "Нет";
      groups.forEach(function (v) {
        var active = v.group.routes.filter(routeOn).length;
        v.box.querySelector(".column-group__meta").textContent = active + " из " + v.group.routes.length + " в работе · " +
          v.duties.filter(function (d) { return P.st.route[d.route_id]; }).length + " нарядов · " +
          v.fleet.length + " " + D.plural(v.fleet.length, "автобус", "автобуса", "автобусов") + " и " +
          v.drivers.length + " " + D.plural(v.drivers.length, "водитель", "водителя", "водителей") + " закреплено";
        if (v.schedule.open) paintSchedule(v);
      });
      refresh();
    }
    el.querySelectorAll("[data-route]").forEach(function (cb) {
      cb.addEventListener("change", function () {
        mark("route", [cb.getAttribute("data-route")], cb.checked);
        cb.closest("tr").classList.toggle("is-off", !cb.checked);
        syncAll();
      });
    });
    all.addEventListener("change", function () {
      var target = all.checked;
      var change = Object.keys(P.st.route).filter(function (k) { return P.st.route[k] !== target; });
      if (change.length) mark("route", change, target);
      el.querySelectorAll("[data-route]").forEach(function (cb) { cb.checked = target; cb.closest("tr").classList.toggle("is-off", !target); });
      syncAll();
    });
    syncAll();
  }

  // Группы: по маршруту закрепления; автобусы и водители без закрепления - отдельной группой.
  // Группа без закрепления - своя у каждого парка: none-P07.
  function groupsOf(items, groupOf) {
    var by = {}, order = [];
    P.ids.forEach(function (id) {
      P.base.routes.forEach(function (r) { if (r.park_id === id) order.push(r.id); });
      order.push("none-" + id);
    });
    items.forEach(function (x) { var k = groupOf(x); (by[k] = by[k] || []).push(x); });
    return order.filter(function (k) { return by[k]; }).map(function (k) { return { id: k, items: by[k] }; });
  }
  function groupTitle(id) {
    var multi = P.ids.length > 1;
    if (id.indexOf("none-") === 0) {
      var pk = id.slice(5);
      return '<span class="num num--none">Р</span><span>Резерв' + (multi ? ' <em class="pk-tag">' + esc(shortName(parkById(pk))) + "</em>" : "") + "</span>";
    }
    var r = P.base.routes.filter(function (x) { return x.id === id; })[0], g = D.geoFor(r.park_id, r.number);
    return chip(r, g) + "<span>Маршрут " + esc(r.number) + (multi ? ' <em class="pk-tag">' + esc(shortName(parkById(r.park_id))) + "</em>" : "") +
      (routeOn(r) ? "" : ' <em class="off">сегодня не работает</em>') + "</span>";
  }

  // Поиск и фильтр. Без фильтра - группы по маршрутам (свёрнуты); с фильтром или поиском -
  // один плоский список найденного, чтобы сразу было видно, что показано и сколько.
  function toolbar(kind, filters) {
    return '<div class="tools"><input class="ad-input tools__q" type="search" placeholder="' + (kind === "veh" ? "Бортовой или госномер" : "Табельный номер или фамилия") +
      '" aria-label="Поиск" data-q="' + kind + '" value="' + esc(P.q[kind]) + '">' +
      '<div class="seg" role="radiogroup">' + filters.map(function (f) {
        return '<button type="button" role="radio" aria-checked="' + (P.filter[kind] === f[0]) + '" data-f="' + f[0] + '" class="' + (P.filter[kind] === f[0] ? "is-on" : "") + '">' + f[1] + ' <span class="seg__n" data-fn="' + f[0] + '"></span></button>';
      }).join("") + '</div></div><div class="found" data-found></div><div class="groups"></div>';
  }
  function bindToolbar(el, kind, rerender) {
    var q = el.querySelector("[data-q]"), t = null;
    q.addEventListener("input", function () { clearTimeout(t); t = setTimeout(function () { P.q[kind] = q.value.trim().toLowerCase(); rerender(); }, 150); });
    el.querySelectorAll("[data-f]").forEach(function (b) {
      b.addEventListener("click", function () {
        P.filter[kind] = b.getAttribute("data-f");
        el.querySelectorAll("[data-f]").forEach(function (x) { var on = x === b; x.classList.toggle("is-on", on); x.setAttribute("aria-checked", String(on)); });
        rerender();
      });
    });
  }
  // Переключатель состояния в строке: «Исправен / Неисправен», «Выходит / Не выходит»
  function toggle(kind, id, on, yes, no) {
    return '<label class="sw sw--text"><input type="checkbox" data-' + kind + '="' + id + '"' + (on ? " checked" : "") + '><span></span><em>' + (on ? yes : no) + "</em></label>";
  }
  function bindToggles(box, kind, yes, no, set) {
    box.querySelectorAll("[data-" + kind + "]").forEach(function (cb) {
      cb.addEventListener("change", function () {
        set(cb.getAttribute("data-" + kind), cb.checked);
        cb.parentNode.querySelector("em").textContent = cb.checked ? yes : no;
        cb.closest("tr").classList.toggle("is-off", !cb.checked);
        // строку под курсором не убираем из списка, а числа у фильтров обновляем сразу
        var body = box.closest(".lp__body"); if (body) listCounts(body, kind, kind === "veh" ? VEH : DRV);
        refresh();
      });
    });
  }
  function routeCell(rid) {
    if (!rid) return '<span class="muted">-</span>';
    var r = P.base.routes.filter(function (x) { return x.id === rid; })[0];
    return r ? chip(r, D.geoFor(r.park_id, r.number)) : "";
  }

  // Общая сборка списка: группы или плоский список, счётчики фильтра
  function listBody(el, kind, cfg) {
    if (!el.querySelector(".groups")) { el.innerHTML = toolbar(kind, cfg.filters); bindToolbar(el, kind, function () { listBody(el, kind, cfg); }); }
    var all = cfg.items(), q = P.q[kind], f = P.filter[kind];
    var byText = all.filter(function (x) { return !q || cfg.text(x).indexOf(q) >= 0; });
    cfg.filters.forEach(function (fl) {
      var n = byText.filter(function (x) { return cfg.pass(x, fl[0]); }).length;
      el.querySelector('[data-fn="' + fl[0] + '"]').textContent = D.num(n);
    });
    var shown = byText.filter(function (x) { return cfg.pass(x, f); });
    var box = el.querySelector(".groups"), found = el.querySelector("[data-found]");
    if (q || f !== "all") {
      found.textContent = "Найдено: " + D.num(shown.length);
      found.hidden = false;
      box.innerHTML = shown.length ? '<div class="grp grp--flat"><div class="grp__rows"></div></div>' : '<p class="muted empty">Ничего не найдено</p>';
      if (shown.length) cfg.rows(box.querySelector(".grp__rows"), shown, true);
    } else {
      found.hidden = true;
      var groups = groupsOf(all, cfg.group);
      box.innerHTML = groups.map(function (g) {
        return '<details class="grp" data-pin data-group="' + kind + ":" + g.id + '"' + (P.open[kind + ":" + g.id] ? " open" : "") + '><summary><span class="grp__t">' + groupTitle(g.id) +
          '</span><span class="grp__n"></span>' +
          '<i class="ad-icon ad-icon--caret-down ad-icon--sm grp__car"></i></summary><div class="grp__rows"></div></details>';
      }).join("");
      box.querySelectorAll("details.grp").forEach(function (d) {
        var gid = d.getAttribute("data-group").split(":")[1], items = groups.filter(function (g) { return g.id === gid; })[0].items;
        var fill = function () { var rows = d.querySelector(".grp__rows"); if (!rows.childElementCount) cfg.rows(rows, items, false); };
        if (d.open) fill();
        d.addEventListener("details:open", fill);
        d.addEventListener("toggle", function () { P.open[kind + ":" + gid] = d.open; if (d.open) fill(); });
      });
    }
    M.swap(box);
    refresh();
  }
  function listCounts(el, kind, cfg) {
    var q = P.q[kind], byText = cfg.items().filter(function (x) { return !q || cfg.text(x).indexOf(q) >= 0; });
    cfg.filters.forEach(function (fl) { var c = el.querySelector('[data-fn="' + fl[0] + '"]'); if (c) c.textContent = D.num(byText.filter(function (x) { return cfg.pass(x, fl[0]); }).length); });
  }

  // Таблица строк с сортировкой: клик по заголовку - сортировать по колонке, повторный - в обратную сторону.
  // Сортировка одна на раздел и действует и в группах, и в плоском списке.
  var collator = new Intl.Collator("ru", { numeric: true, sensitivity: "base" });
  function cmp(a, b) { return typeof a === "number" && typeof b === "number" ? a - b : collator.compare(String(a), String(b)); }
  function routeSortKey(rid) {
    var r = rid && P.base.routes.filter(function (x) { return x.id === rid; })[0];
    return r ? r.number : "яяя";  // резерв - в конце
  }
  function parkNum(id) { return id.replace(/^P0?/, "№"); }
  function sortItems(kind, cols, items) {
    var st = P.sort[kind], col = cols.filter(function (c) { return c.key === st.key; })[0] || cols[0];
    var tie = cols.filter(function (c) { return c.key === (kind === "veh" ? "board" : "name"); })[0];
    return items.slice().sort(function (a, b) { return st.dir * cmp(col.sort(a), col.sort(b)) || cmp(tie.sort(a), tie.sort(b)); });
  }
  function renderTable(box, kind, cols, items, flat) {
    var use = cols.filter(function (c) { return flat || !c.flatOnly; });
    var sorted = sortItems(kind, cols, items), st = P.sort[kind];
    var MAX = 300, more = sorted.length - MAX;
    box.innerHTML = '<table class="rows"><thead><tr>' + use.map(function (c) {
      var on = st.key === c.key;
      return c.sort ? '<th aria-sort="' + (on ? (st.dir > 0 ? "ascending" : "descending") : "none") + '"><button type="button" class="th-sort' + (on ? " is-on" : "") + '" data-sort="' + c.key + '">' + c.title +
        '<i class="th-sort__ic" aria-hidden="true"></i></button></th>' : "<th>" + c.title + "</th>";
    }).join("") + "</tr></thead><tbody>" + sorted.slice(0, MAX).map(function (x) {
      return '<tr class="' + (cols.on(x) ? "" : "is-off") + '">' + use.map(function (c) { return "<td>" + c.cell(x) + "</td>"; }).join("") + "</tr>";
    }).join("") + "</tbody></table>" + (more > 0 ? '<p class="muted more">Показаны первые ' + MAX + " из " + D.num(sorted.length) + " - уточните поиск</p>" : "");
    box.querySelectorAll("[data-sort]").forEach(function (b) {
      b.addEventListener("click", function () {
        var k = b.getAttribute("data-sort");
        P.sort[kind] = { key: k, dir: P.sort[kind].key === k ? -P.sort[kind].dir : 1 };
        var body = box.closest(".lp__body"); if (body) listBody(body, kind, kind === "veh" ? VEH : DRV);
      });
    });
    cols.bind(box);
  }

  // --- автобусы ---
  function vehCols() {
    var c = [
      { key: "park", title: "Парк", cell: function (v) { return parkNum(v.park_id); }, sort: function (v) { return v.park_id; } },
      { key: "board", title: "Борт", cell: function (v) { return Ops.recordButton("veh", v.id, v.park_id, v.board_number); }, sort: function (v) { return +v.board_number || v.board_number; } },
      { key: "route", title: "Маршрут", flatOnly: true, cell: function (v) { return routeCell(v.home_route_id); }, sort: function (v) { return routeSortKey(v.home_route_id); } },
      { key: "plate", title: "Госномер", cell: function (v) { return esc(v.plate); }, sort: function (v) { return v.plate; } },
      { key: "model", title: "Модель", cell: function (v) { return '<span class="muted">' + esc(v.model) + "</span>"; }, sort: function (v) { return v.model; } },
      { key: "class", title: "Класс", cell: function (v) { return D.CLASSES[v.class]; }, sort: function (v) { return D.CLASSES[v.class]; } },
      { key: "fuel", title: "Топливо", cell: function (v) { return v.fuel === "gas" ? "газ" : v.fuel === "electric" ? "электро" : "дизель"; }, sort: function (v) { return v.fuel; } },
      { key: "state", title: "Состояние", cell: function (v) { return toggle("veh", v.id, vehOk(v), "Исправен", "Неисправен"); }, sort: function (v) { return vehOk(v) ? 0 : 1; } }
    ];
    c.on = vehOk;
    c.bind = function (box) { bindToggles(box, "veh", "Исправен", "Неисправен", function (id, on) { mark("veh", [id], on); }); };
    return c;
  }
  var VEH = {
    filters: [["all", "Все"], ["ok", "Исправные"], ["bad", "Неисправные"]],
    items: function () { return P.base.vehicles; },
    text: function (v) { return (v.board_number + " " + v.plate.replace(/\s/g, "")).toLowerCase(); },
    pass: function (v, f) { return f === "all" || (f === "ok") === vehOk(v); },
    group: function (v) { return vehGroup(v); },
    rows: function (box, items, flat) { renderTable(box, "veh", vehCols(), items, flat); }
  };
  function vehBody(el) { listBody(el, "veh", VEH); }

  // --- водители ---
  function vehGroup(v) { return v.home_route_id || "none-" + v.park_id; }
  function drvGroup(d) { return drvRoute(d) || "none-" + d.park_id; }
  function drvRoute(d) { var v = d.home_vehicle_id && P.vehById[d.home_vehicle_id]; return v ? v.home_route_id : null; }
  function drvCols() {
    var c = [
      { key: "park", title: "Парк", cell: function (d) { return parkNum(d.park_id); }, sort: function (d) { return d.park_id; } },
      { key: "tab", title: "Таб. №", cell: function (d) { return esc(d.tab_number); }, sort: function (d) { return d.tab_number; } },
      { key: "name", title: "Водитель", cell: function (d) { return Ops.recordButton("drv", d.id, d.park_id, d.full_name); }, sort: function (d) { return d.full_name; } },
      { key: "route", title: "Маршрут", flatOnly: true, cell: function (d) { return routeCell(drvRoute(d)); }, sort: function (d) { return routeSortKey(drvRoute(d)); } },
      { key: "class", title: "Допуск", cell: function (d) { return d.classes.filter(function (c) { return c !== "medium"; }).map(function (c) { return D.CLASSES[c]; }).join(", "); },
        sort: function (d) { return d.classes.length; } },
      { key: "bus", title: "Закреплённый автобус", cell: function (d) { var v = d.home_vehicle_id && P.vehById[d.home_vehicle_id]; return v ? Ops.recordButton("veh", v.id, v.park_id, v.board_number) : '<span class="muted">—</span>'; },
        sort: function (d) { var v = d.home_vehicle_id && P.vehById[d.home_vehicle_id]; return v ? +v.board_number : 99999; } },
      { key: "state", title: "Сегодня", cell: function (d) { return toggle("drv", d.id, drvGoes(d), "Выходит", "Не выходит"); }, sort: function (d) { return drvGoes(d) ? 0 : 1; } }
    ];
    c.on = drvGoes;
    c.bind = function (box) { bindToggles(box, "drv", "Выходит", "Не выходит", function (id, on) { mark("drv", [id], on); }); };
    return c;
  }
  var DRV = {
    filters: [["all", "Все"], ["on", "Выходят"], ["off", "Не выходят"]],
    items: function () { return P.base.drivers; },
    text: function (d) { return (d.tab_number + " " + d.full_name).toLowerCase(); },
    pass: function (d, f) { return f === "all" || (f === "on") === drvGoes(d); },
    group: function (d) { return drvGroup(d); },
    rows: function (box, items, flat) { renderTable(box, "drv", drvCols(), items, flat); }
  };
  function drvBody(el) {
    listBody(el, "drv", DRV);
  }

  function updateGroupHead(det) {
    var parts = det.getAttribute("data-group").split(":"), kind = parts[0], gid = parts[1], n = det.querySelector(".grp__n");
    if (kind === "veh") {
      var vs = P.base.vehicles.filter(function (v) { return vehGroup(v) === gid; });
      var ok = vs.filter(vehOk).length;
      n.innerHTML = '<span class="' + (ok < vs.length ? "warn" : "") + '">исправны ' + ok + " из " + vs.length + "</span>";
    } else {
      if (!P.vehById) return;
      var ds = P.base.drivers.filter(function (d) { return drvGroup(d) === gid; });
      n.textContent = "выходят " + ds.filter(drvGoes).length + " из " + ds.length;
    }
  }

  // ---------- день для движка ----------
  function buildDay() {
    var d = JSON.parse(JSON.stringify(P.base)), st = P.st;
    var gone = {};
    d.duties = d.duties.filter(function (x) { if (x.type === "line" && !st.route[x.route_id]) { gone[x.id] = 1; return false; } return true; });
    d.shifts = d.shifts.filter(function (s) { return !gone[s.duty_id]; });
    // выпуск парка = наряды, которые сегодня есть; иначе проверка данных ругается на расхождение с планом
    var kind = d.meta.day_type;
    d.parks.forEach(function (p) { p["release_" + kind] = d.duties.filter(function (x) { return x.park_id === p.id; }).length; });
    d.routes.forEach(function (r) { if (!st.route[r.id]) r["duties_" + kind] = 0; });
    d.vehicles.forEach(function (v) {
      if (st.veh[v.id]) { v.condition = "ok"; v.repair_days_left = null; }
      else if (v.condition === "ok") { v.condition = "repair"; v.repair_days_left = 1; }
    });
    d.drivers.forEach(function (x) {
      if (st.drv[x.id]) { x.schedule = "work"; x.medical = "passed"; }
      else if (x.schedule === "work" && x.medical === "passed") x.medical = "failed";  // в графике, но на линию не выходит
    });
    d.meta.source = (P.file ? "реестр из файла «" + P.file + "»" : "реестр " + P.ids.join(", ")) + " на " + P.date + " с отметками диспетчера";
    return d;
  }

  $("#btnCalc").addEventListener("click", function () {
    if (!P.base) return;  // реестр ещё грузится
    var c = counts();
    if (!c.lineDuties) return toast("Ни один маршрут не работает - считать нечего", true);
    confirmRecalc().then(function (ok) {
      if (!ok) return;
      $("#prepBusy").hidden = false; $("#prepBusyText").textContent = "Расчёт расстановки…";
      // Не сохранились отметки - считаем всё равно, по тому, что отмечено на экране: сообщение
      // «Не сохранилось» уже показано, а «Сохранить» осталась с числом, и сохранение можно повторить.
      save().catch(function () {}).then(function () { return window.App.run(buildDay(), function () { P.calculated = true; go("result"); }); }).then(function () {
        $("#prepBusy").hidden = true;
      }, function (e) {
        $("#prepBusy").hidden = true;
        toast(e.message + (e.details && e.details.length ? ": " + e.details.slice(0, 2).join("; ") : ""), true);
      });
    });
  });
  // Повторный расчёт того же дня тех же парков заменяет прежний: сходы, замены и правки дня пропадают.
  // Если они были - спросить. Сервер не ответил - не мешаем расчёту.
  function confirmRecalc() {
    var day = P.base, parks = day.parks.map(function (p) { return p.id; });
    return D.api("GET", "/x/day-log?date=" + encodeURIComponent(day.meta.date) + "&parks=" + parks.map(encodeURIComponent).join(",")).then(function (r) {
      var n = r.events + r.edits;
      if (!n) return true;
      return D.events.confirm({ title: "Рассчитать заново?",
        text: "Сходы, замены и правки этого дня пропадут: " + n + " " + D.plural(n, "запись", "записи", "записей") + " в журнале.",
        ok: "Рассчитать заново", cancel: "Отмена", danger: true });
    }, function () { return true; });
  }

  // ---------- старт ----------
  // Заставка: короткая линия загрузки, пока приходит список парков; не меньше 500 мс, чтобы не мигала.
  var started = Date.now();
  function hideSplash() {
    setTimeout(function () {
      var sp = $("#splash"); sp.classList.add("is-done"); setTimeout(function () { sp.remove(); }, 400);
      // вход: группы и карточки появляются каскадом, когда заставка уходит
      if (current === "parks") { M.stagger($("#parks").querySelectorAll(".kind")); M.stagger($("#parks").querySelectorAll(".pc")); }
    }, Math.max(0, 500 - (Date.now() - started)));
  }
  Promise.all([D.loadParks(), D.loadGeo()]).then(function (r) { var list = r[0];
    // доступные парки - первыми
    P.parks = list.slice().sort(function (a, b) { return (b.available ? 1 : 0) - (a.available ? 1 : 0); });
    renderParks();
    firstRoute(); hideSplash();
  }, function (e) { $("#parks").innerHTML = '<p class="err">' + esc(e.message) + "</p>"; hideSplash(); });
})();
