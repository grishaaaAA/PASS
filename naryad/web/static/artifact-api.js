/* Страница по ссылке: данные считает генератор внутри страницы, файлы сохраняет claude.ai. */
(function () {
  "use strict";
  var G = window.AutoDispGenerator;
  var downloads = window.claude && window.claude.use ? window.claude.use("downloads") : Promise.resolve(null);
  function later(fn) {  // даём странице перерисоваться («Генерируем…») до тяжёлого расчёта
    return new Promise(function (resolve, reject) {
      setTimeout(function () { try { resolve(fn()); } catch (e) { reject(e); } }, 30);
    });
  }
  window.AUTODISP_API = {
    local: false,
    defaults: function () { return Promise.resolve(G.defaults()); },
    generate: function (raw) { return later(function () { return G.summarize(G.parseRequest(raw)); }); },
    save: function (raw, format) {
      return later(function () { return G.exportFile(G.parseRequest(raw), format); }).then(function (file) {
        return downloads.then(function (d) {
          if (!d) throw new Error("скачивание файлов здесь недоступно - откройте страницу в браузере на claude.ai");
          return d.save({ filename: file.name, data: file.data }).catch(function (err) {
            if (err && err.code === "declined") return;  // зритель передумал - это не ошибка
            throw new Error("файл не сохранён (" + ((err && err.code) || "ошибка") + ")");
          });
        });
      });
    }
  };
})();
