/**
 * Tablet mode + extended live alerts — shared overlay / commentator
 */
(function () {
  const FONT_KEY = "tabletFontScale";
  const WAKE_KEY = "tabletWakeOn";
  const ALERTS_KEY = "broadcastAlerts";

  const MIN_SCALE = 0.85;
  const MAX_SCALE = 1.45;
  const STEP = 0.1;

  let wakeLock = null;
  let wakeOn = localStorage.getItem(WAKE_KEY) === "1";
  let alertsOn = localStorage.getItem(ALERTS_KEY) === "1";
  let seenEvents = new Set();
  let seenMoments = new Set();
  let alertFixtureId = null;
  let deferredInstall = null;

  function playBeep(freq) {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      o.connect(g);
      g.connect(ctx.destination);
      o.frequency.value = freq || 880;
      g.gain.setValueAtTime(0.12, ctx.currentTime);
      g.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.35);
      o.start();
      o.stop(ctx.currentTime + 0.35);
    } catch (e) {}
  }

  function fireAlert(title, body, freq) {
    if (Notification.permission === "granted") {
      new Notification(title, { body, icon: "/static/icon.svg" });
    }
    playBeep(freq);
  }

  function getScale() {
    const v = parseFloat(localStorage.getItem(FONT_KEY) || "1");
    return Number.isFinite(v) ? v : 1;
  }

  function applyFontScale(scale) {
    const clamped = Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale));
    document.documentElement.style.setProperty("--font-scale", String(clamped));
    localStorage.setItem(FONT_KEY, String(clamped));
    const label = document.getElementById("font-scale-label");
    if (label) label.textContent = Math.round(clamped * 100) + "%";
    return clamped;
  }

  async function acquireWakeLock() {
    if (!("wakeLock" in navigator)) return false;
    try {
      wakeLock = await navigator.wakeLock.request("screen");
      wakeLock.addEventListener("release", () => {
        wakeLock = null;
        updateWakeBtn();
      });
      return true;
    } catch (e) {
      return false;
    }
  }

  async function releaseWakeLock() {
    if (wakeLock) {
      try {
        await wakeLock.release();
      } catch (e) {}
      wakeLock = null;
    }
  }

  function updateWakeBtn() {
    const btn = document.getElementById("wake-btn");
    if (!btn) return;
    const active = !!wakeLock;
    btn.classList.toggle("on", active);
    btn.textContent = active ? "☀️ Екран: активен" : "☀️ Екранът да не гаси";
  }

  function updateAlertsBtn() {
    const btn = document.getElementById("alert-btn");
    if (!btn) return;
    btn.classList.toggle("on", alertsOn);
    btn.textContent = alertsOn ? "🔔 Аларми: включени" : "🔔 Аларми (live)";
  }

  function isRedCard(detail) {
    const d = (detail || "").toLowerCase();
    return d.includes("red") || d.includes("second yellow");
  }

  function isPenaltyRelated(ev) {
    const det = (ev.detail || "").toLowerCase();
    const typ = (ev.type || "").toLowerCase();
    if (typ === "var") return true;
    return det.includes("penalty") || det.includes("дузп");
  }

  function checkAlerts(data) {
    if (!alertsOn || !data || data.phase !== "live") return;

    const fid = data.match?.raw_id;
    if (fid !== alertFixtureId) {
      seenEvents.clear();
      seenMoments.clear();
      alertFixtureId = fid;
    }

    const matchMinute = parseInt(data.match?.minute, 10) || 0;

    for (const ev of data.events || []) {
      const typ = ev.type || "";
      const det = ev.detail || "";
      const key = `ev|${typ}|${ev.minute}|${ev.team}|${ev.player}|${det}`;
      if (seenEvents.has(key)) continue;

      if (typ === "Goal") {
        seenEvents.add(key);
        fireAlert(
          `⚽ ГОЛ ${ev.minute}'`,
          `${ev.team} — ${ev.message || ev.player || det}`,
          880
        );
        continue;
      }

      if (typ === "Card" && isRedCard(det)) {
        seenEvents.add(key);
        fireAlert(
          `🟥 ЧЕРВЕН КАРТОН ${ev.minute}'`,
          `${ev.team} — ${ev.player || ev.message || det}`,
          520
        );
        continue;
      }

      if (isPenaltyRelated(ev)) {
        seenEvents.add(key);
        fireAlert(
          `📺 VAR / ДУЗПА ${ev.minute}'`,
          `${ev.team} — ${det || ev.message || ev.player || "Проверка"}`,
          660
        );
        continue;
      }

      if (typ === "subst" && matchMinute > 0) {
        const evMin = parseInt(ev.minute, 10) || 0;
        if (matchMinute - evMin <= 5) {
          seenEvents.add(key);
          fireAlert(
            `🔄 СМЯНА ${ev.minute}'`,
            `${ev.team} — ${ev.player || ev.message || "нов играч"}`,
            740
          );
        }
      }
    }

    for (const km of data.key_moments || []) {
      if (!["high", "critical"].includes(km.severity)) continue;
      const key = `km|${km.type}|${matchMinute}|${km.title}`;
      if (seenMoments.has(key)) continue;
      seenMoments.add(key);
      fireAlert(km.title || "Ключов момент", km.message || "", 900);
    }
  }

  function resetAlertsForMatch() {
    seenEvents.clear();
    seenMoments.clear();
    alertFixtureId = null;
  }

  async function toggleWakeLock() {
    if (wakeLock) {
      wakeOn = false;
      localStorage.setItem(WAKE_KEY, "0");
      await releaseWakeLock();
      updateWakeBtn();
      return;
    }
    const ok = await acquireWakeLock();
    if (ok) {
      wakeOn = true;
      localStorage.setItem(WAKE_KEY, "1");
    }
    updateWakeBtn();
  }

  function toggleAlerts() {
    alertsOn = !alertsOn;
    localStorage.setItem(ALERTS_KEY, alertsOn ? "1" : "0");
    updateAlertsBtn();
    if (typeof window._refreshPreflight === "function") window._refreshPreflight();
    if (alertsOn && "Notification" in window && Notification.permission === "default") {
      Notification.requestPermission();
    }
    if (alertsOn && wakeOn && !wakeLock) acquireWakeLock();
  }

  function changeFont(delta) {
    applyFontScale(getScale() + delta);
  }

  async function installPwa() {
    if (deferredInstall) {
      deferredInstall.prompt();
      await deferredInstall.userChoice;
      deferredInstall = null;
      const btn = document.getElementById("pwa-install-btn");
      if (btn) btn.style.display = "none";
      return;
    }
    alert("iOS: Safari → Сподели → „На началния екран“. Android/Chrome: меню → Инсталирай приложение.");
  }

  function registerServiceWorker() {
    if (!("serviceWorker" in navigator)) return;
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }

  function initResponsiveSidebar() {
    const sidebar = document.getElementById("sidebar");
    const toggle = document.getElementById("sidebar-toggle");
    const backdrop = document.getElementById("sidebar-backdrop");
    if (!sidebar || !toggle) return;

    const MQ = window.matchMedia("(max-width: 1200px)");

    function isDrawer() {
      return MQ.matches;
    }

    function setOpen(open) {
      const shouldOpen = open && isDrawer();
      sidebar.classList.toggle("open", shouldOpen);
      if (backdrop) backdrop.classList.toggle("show", shouldOpen);
      document.body.classList.toggle("sidebar-open", shouldOpen);
      toggle.setAttribute("aria-expanded", shouldOpen ? "true" : "false");
      if (typeof window.updateSidebarScrollHint === "function") {
        requestAnimationFrame(() => window.updateSidebarScrollHint());
      }
      if (shouldOpen && isDrawer()) {
        const pane = document.getElementById("sidebar-inner");
        if (pane) pane.scrollTop = 0;
      }
    }

    function closeSidebar() {
      setOpen(false);
    }

    function openSidebar() {
      if (isDrawer()) setOpen(true);
    }

    function toggleSidebar() {
      setOpen(!sidebar.classList.contains("open"));
    }

    toggle.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      toggleSidebar();
    });

    if (backdrop) {
      backdrop.addEventListener("click", closeSidebar);
    }

    MQ.addEventListener("change", () => {
      if (!isDrawer()) closeSidebar();
    });

    window.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeSidebar();
    });

    window.ResponsiveLayout = { closeSidebar, openSidebar, isDrawer };
  }

  let initialized = false;

  function init() {
    if (initialized) return;
    initialized = true;

    document.body.classList.add("tablet-mode");
    applyFontScale(getScale());
    registerServiceWorker();
    updateAlertsBtn();
    updateWakeBtn();
    initResponsiveSidebar();

    if (wakeOn) acquireWakeLock();

    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible" && wakeOn && !wakeLock) {
        acquireWakeLock();
      }
    });

    window.addEventListener("beforeinstallprompt", (e) => {
      e.preventDefault();
      deferredInstall = e;
      const btn = document.getElementById("pwa-install-btn");
      if (btn) btn.style.display = "inline-flex";
    });
  }

  window.TabletMode = {
    init,
    changeFont,
    toggleWakeLock,
    toggleAlerts,
    installPwa,
    checkAlerts,
    resetAlertsForMatch,
    isAlertsOn: () => alertsOn,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
