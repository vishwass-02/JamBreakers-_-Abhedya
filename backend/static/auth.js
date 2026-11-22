// ABHEDYA sign-in helper. Every screen loads this first (<script src="/auth.js">).
// On the demo laptop nothing changes: the server trusts it. On any other device the session token from /login is
// attached to API calls, downloads and the WebSocket; without one the screen is sent to /login.
(function () {
  'use strict';
  var KEY = 'abhedya_session';
  function load() { try { return JSON.parse(localStorage.getItem(KEY) || 'null'); } catch (e) { return null; } }
  function token() { var s = load(); return s && s.exp > Date.now() ? s.token : ''; }
  function toLogin() {
    if (location.pathname === '/login') return;
    location.replace('/login?next=' + encodeURIComponent(location.pathname + location.search + location.hash));
  }
  window.ABHEDYA_AUTH = {
    token: token,
    save: function (r) { try { localStorage.setItem(KEY, JSON.stringify({ token: r.token, user: r.user, role: r.role, exp: Date.now() + (r.expires_in || 0) * 1000 })); } catch (e) {} },
    clear: function () { try { localStorage.removeItem(KEY); } catch (e) {} },
    signOut: function () {
      var t = token(); window.ABHEDYA_AUTH.clear();
      fetch('/api/logout', { method: 'POST', headers: t ? { Authorization: 'Bearer ' + t } : {} }).finally(toLogin);
    },
    withToken: function (url) {
      var t = token(); if (!t) return url;
      return url + (url.indexOf('?') < 0 ? '?' : '&') + 'token=' + encodeURIComponent(t);
    },
  };

  // API calls: add the token; a 401 means the session is gone
  var _fetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    var url = typeof input === 'string' ? input : (input && input.url) || '';
    var same = url.charAt(0) === '/' || url.indexOf(location.origin) === 0;
    if (same && /\/(api|results)\//.test(url)) {
      var t = token();
      if (t) { init = init || {}; var h = new Headers(init.headers || {}); h.set('Authorization', 'Bearer ' + t); init.headers = h; }
      return _fetch(input, init).then(function (r) {
        if (r.status === 401 && url.indexOf('/api/me') < 0 && url.indexOf('/api/login') < 0) toLogin();
        return r;
      });
    }
    return _fetch(input, init);
  };

  // WebSocket: token in the query string (browsers cannot set headers); close code 4401 = sign in again
  var _WS = window.WebSocket;
  function WS(url, protocols) {
    if (/\/ws(\?|$)/.test(url)) url = window.ABHEDYA_AUTH.withToken(url);
    var ws = protocols === undefined ? new _WS(url) : new _WS(url, protocols);
    ws.addEventListener('close', function (e) { if (e.code === 4401) toLogin(); });
    return ws;
  }
  WS.prototype = _WS.prototype;
  ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'].forEach(function (k) { WS[k] = _WS[k]; });
  window.WebSocket = WS;

  // download links (<a href="/api/report">) cannot carry a header: add the token at click time
  document.addEventListener('click', function (e) {
    var a = e.target && e.target.closest ? e.target.closest('a[href]') : null;
    if (!a) return;
    var href = a.getAttribute('href');
    if (href && /^\/(api|results)\//.test(href) && href.indexOf('token=') < 0 && token()) a.setAttribute('href', window.ABHEDYA_AUTH.withToken(href));
  }, true);

  // not on the laptop and not signed in: go to the sign-in page before the screen tries to connect
  if (location.pathname !== '/login') {
    _fetch('/api/me', { headers: token() ? { Authorization: 'Bearer ' + token() } : {} })
      .then(function (r) { if (r.status === 401) toLogin(); return r.ok ? r.json() : null; })
      .then(function (me) { window.ABHEDYA_ME = me; if (me) document.dispatchEvent(new CustomEvent('abhedya-me', { detail: me })); })
      .catch(function () {});
  }
})();
