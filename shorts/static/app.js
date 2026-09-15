/* 숏폼 렌더러 대시보드 */
const $ = (id) => document.getElementById(id);
const api = async (url, opt) => {
  const r = await fetch(url, opt);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
  return r.json();
};

// 상태: 클립별 설정을 파일명 기준으로 보관
let clips = [];                 // [{name, duration, size_mb}]
let sel = new Map();            // name -> {on, order, captions:[], trim:null}
let cur = null;                 // 현재 편집 중인 클립명

const fmt = (t) => (t ?? 0).toFixed(2);

/* ── 환경 점검 ─────────────────────────────────────────── */
async function loadEnv() {
  const e = await api("/api/env");
  $("env").innerHTML =
    `<span class="${e.ffmpeg ? "good" : "bad"}">ffmpeg ${e.ffmpeg ? "확인" : "없음"}</span>` +
    `<span class="${e.font ? "good" : "bad"}">폰트 ${e.font ? "확인" : "없음"}</span>` +
    `<span class="path">${e.clips_dir}</span>`;
  if (!e.ffmpeg) banner("err", "ffmpeg 를 찾을 수 없습니다. 설치 후 서버를 다시 시작하세요.");
  else if (!e.font) banner("err", "한글 폰트를 찾지 못했습니다. config.yaml 에 font 경로를 지정하세요.");
}

function banner(kind, text) {
  $("msg").innerHTML = `<div class="banner ${kind}">${text}</div>`;
}

/* ── 클립 목록 ─────────────────────────────────────────── */
async function loadClips() {
  clips = await api("/api/clips");
  clips.forEach((c, i) => {
    if (!sel.has(c.name)) sel.set(c.name, { on: false, order: i, captions: [], trim: null, trimOn: true });
  });
  renderClips();
}

function chosen() {
  return clips.filter((c) => sel.get(c.name)?.on)
              .sort((a, b) => sel.get(a.name).order - sel.get(b.name).order);
}

function renderClips() {
  const box = $("clips");
  if (!clips.length) { box.innerHTML = `<div class="empty">clips 폴더가 비어 있습니다.</div>`; return; }
  const sorted = [...clips].sort((a, b) => sel.get(a.name).order - sel.get(b.name).order);
  box.innerHTML = "";
  sorted.forEach((c, idx) => {
    const s = sel.get(c.name);
    const el = document.createElement("div");
    el.className = "clip" + (cur === c.name ? " active" : "");
    el.innerHTML = `
      <input type="checkbox" ${s.on ? "checked" : ""}>
      <img src="/api/thumb/${encodeURIComponent(c.name)}" alt="">
      <div class="meta">
        <div class="nm" title="${c.name}">${c.name}</div>
        <div class="sub">${c.duration}초 · ${c.size_mb}MB${trimActive(s) ? " · 구간 " + s.trim[0] + "~" + s.trim[1] + "초" : ""}${s.captions.length ? ` · 자막 ${s.captions.length}` : ""}</div>
      </div>
      <div class="order">
        <button data-up ${idx === 0 ? "disabled" : ""}>▲</button>
        <button data-dn ${idx === sorted.length - 1 ? "disabled" : ""}>▼</button>
      </div>`;
    const cb = el.querySelector("input");
    // 목록 전체를 다시 그리면 포커스·스크롤이 튀므로 상태만 바꾼다
    cb.onchange = (ev) => { s.on = ev.target.checked; updateTrimUI(); };
    cb.onclick = (ev) => ev.stopPropagation();
    el.querySelector("[data-up]").onclick = (ev) => { ev.stopPropagation(); swap(sorted, idx, idx - 1); };
    el.querySelector("[data-dn]").onclick = (ev) => { ev.stopPropagation(); swap(sorted, idx, idx + 1); };
    el.onclick = () => openClip(c.name);
    box.appendChild(el);
  });
}

function swap(sorted, i, j) {
  if (j < 0 || j >= sorted.length) return;
  const a = sel.get(sorted[i].name), b = sel.get(sorted[j].name);
  [a.order, b.order] = [b.order, a.order];
  renderClips();
}

/* ── 미리보기 ──────────────────────────────────────────── */
function openClip(name) {
  cur = name;
  $("noclip").hidden = true;
  $("editor").hidden = false;
  const v = $("vid");
  // 아이폰 HEVC 원본은 브라우저가 못 읽으므로 서버가 H.264 사본을 만든다.
  // 첫 재생 때는 변환 시간이 걸리니 진행 중임을 알린다.
  showLoading(true);
  v.poster = `/api/thumb/${encodeURIComponent(name)}`;   // 변환 끝나기 전에도 첫 장면을 보여준다
  loadWithFallback(v, name, VIDEO_FMT);
  const s = sel.get(name);
  $("trimA").value = s.trim ? s.trim[0] : "";
  $("trimB").value = s.trim ? s.trim[1] : "";
  $("trimOn").checked = s.trimOn !== false;
  updateTrimUI();
  renderCaps();
  renderClips();
}

function loadWithFallback(v, name, fmt, tried) {
  tried = tried || [];
  tried.push(fmt);
  v.src = `/api/video/${encodeURIComponent(name)}?fmt=${fmt}`;
  v.load();
  v.onloadeddata = () => showLoading(false);
  v.onerror = () => {
    // 고른 형식이 실패하면 다른 형식으로 한 번 더 시도한다
    const next = ["mp4", "webm"].find((f) => !tried.includes(f));
    if (next) { showLoading(true, "다른 형식으로 변환 중..."); loadWithFallback(v, name, next, tried); }
    else showLoading(false, "프리뷰를 재생할 수 없습니다.\n자막 위치와 시간은 그대로 설정할 수 있고, 렌더링 결과에는 영향이 없습니다.");
  };
}

const vid = () => $("vid");

/* 이 브라우저가 재생할 수 있는 형식을 고른다.
   H.264 는 대부분 되지만, 독점 코덱이 빠진 Chromium 빌드에서는 WebM 을 쓴다. */
const VIDEO_FMT = (() => {
  const v = document.createElement("video");
  if (v.canPlayType('video/mp4; codecs="avc1.42E01E"')) return "mp4";
  if (v.canPlayType('video/webm; codecs="vp8,vorbis"')) return "webm";
  return "mp4";
})();

function showLoading(on, text) {
  let el = document.querySelector("#stage .loading");
  if (on || text) {
    if (!el) { el = document.createElement("div"); el.className = "loading"; $("stage").appendChild(el); }
    el.textContent = text || "프리뷰 준비 중...";
  el.style.whiteSpace = "pre-line";
  } else if (el) el.remove();
}

$("play").onclick = () => { const v = vid(); v.paused ? v.play() : v.pause(); };

function syncTime() {
  const v = vid();
  const d = v.duration || 0;
  $("seek").max = d || 100;
  $("seek").value = v.currentTime;
  $("tnow").textContent = `${fmt(v.currentTime)} / ${fmt(d)} 초`;
  $("play").textContent = v.paused ? "재생" : "일시정지";
  drawCapPreview();
}

["timeupdate", "loadedmetadata", "play", "pause", "seeked"].forEach((e) =>
  document.addEventListener(e, (ev) => { if (ev.target.id === "vid") syncTime(); }, true));

$("seek").oninput = () => { vid().currentTime = parseFloat($("seek").value); };

/* 자막 미리보기 오버레이 — 굽기 결과를 근사해서 보여준다 */
function drawCapPreview() {
  const layer = $("capLayer");
  if (!cur) { layer.innerHTML = ""; return; }
  const v = vid(), t = v.currentTime;
  const w = v.clientWidth, h = v.clientHeight;
  const scale = w / 1080;                       // 실제 출력은 1080 폭
  const s = sel.get(cur);
  const off = trimActive(s) ? s.trim[0] : 0;    // 자막 시각은 잘린 구간 기준
  layer.innerHTML = "";
  s.captions.forEach((c) => {
    const a = (c.at?.[0] ?? 0) + off, b = (c.at?.[1] ?? 0) + off;
    if (t < a || t > b) return;
    const d = document.createElement("div");
    d.className = "cap-prev";
    d.textContent = c.text || "";
    d.style.top = `${(c.y ?? 0.8) * h}px`;
    d.style.fontSize = `${(c.size ?? 56) * scale}px`;
    layer.appendChild(d);
  });
}
window.addEventListener("resize", drawCapPreview);

/* ── 구간 자르기 ───────────────────────────────────────── */
$("trimNow").onclick = () => {
  const t = +fmt(vid().currentTime);
  if (!$("trimA").value) $("trimA").value = t; else $("trimB").value = t;
  saveTrim();
};
$("trimClear").onclick = () => { $("trimA").value = ""; $("trimB").value = ""; saveTrim(); };
$("trimA").oninput = $("trimB").oninput = saveTrim;
$("trimOn").onchange = () => {
  if (cur) sel.get(cur).trimOn = $("trimOn").checked;
  updateTrimUI(); renderClips(); drawCapPreview();
};
$("trimAll").onchange = () => { updateTrimUI(); renderClips(); };

/* 구간이 실제로 적용되는 조건: 값이 있고 + 클립별 토글 켜짐 + 전체 토글 켜짐 */
function trimActive(s) {
  return !!(s.trim && s.trimOn !== false && $("trimAll").checked);
}

function updateTrimUI() {
  const s = cur ? sel.get(cur) : null;
  const has = !!(s && s.trim);
  $("trimOn").disabled = !has;
  $("trimOnLabel").textContent = !has
    ? "구간을 먼저 지정하세요"
    : (s.trimOn === false ? "이 구간 무시하고 전체 사용" : "이 구간만 사용");
  const withTrim = chosen().filter((c) => sel.get(c.name).trim);
  const active = withTrim.filter((c) => trimActive(sel.get(c.name))).length;
  const off = withTrim.length - active;
  $("trimSummary").textContent =
    !withTrim.length ? "구간을 지정한 클립이 없습니다. 전체를 씁니다."
    : !active        ? `구간이 지정된 클립 ${withTrim.length}개가 있지만, 지금은 전체를 씁니다.`
    : `클립 ${active}개를 지정 구간만 잘라서 씁니다.` + (off ? ` (${off}개는 해제됨)` : "");
}

function saveTrim() {
  if (!cur) return;
  const a = parseFloat($("trimA").value), b = parseFloat($("trimB").value);
  const s = sel.get(cur);
  const had = !!s.trim;
  s.trim = (!isNaN(a) && !isNaN(b) && b > a) ? [a, b] : null;
  if (s.trim && !had) s.trimOn = true;   // 새로 지정하면 바로 적용
  updateTrimUI(); renderClips(); drawCapPreview();
}

/* ── 자막 편집 ─────────────────────────────────────────── */
$("addCap").onclick = () => {
  if (!cur) return;
  const s = sel.get(cur);
  const off = trimActive(s) ? s.trim[0] : 0;
  const t = Math.max(0, vid().currentTime - off);
  const end = Math.min(t + 3.5,
    (trimActive(s) ? s.trim[1] - s.trim[0] : vid().duration) || t + 3.5);
  s.captions.push({ text: "", at: [+fmt(t), +fmt(end)], y: 0.8, size: 56 });
  renderCaps(); renderClips();
};

function renderCaps() {
  const box = $("caps");
  const s = sel.get(cur);
  if (!s.captions.length) { box.innerHTML = `<div class="empty">자막이 없습니다.</div>`; return; }
  box.innerHTML = "";
  s.captions.forEach((c, i) => {
    const el = document.createElement("div");
    el.className = "cap";
    el.innerHTML = `
      <div class="top">
        <input data-text placeholder="자막 내용" value="${(c.text || "").replace(/"/g, "&quot;")}">
        <button class="del" data-del>삭제</button>
      </div>
      <div class="times">
        <div><label>시작(초)</label><input type="number" step="0.05" min="0" data-a value="${c.at[0]}"></div>
        <div><label>끝(초)</label><input type="number" step="0.05" min="0" data-b value="${c.at[1]}"></div>
        <button class="sm" data-now>현재</button>
      </div>
      <div class="adv">
        <div><label>세로 위치 ${c.y}</label><input type="range" min="0.05" max="0.92" step="0.01" data-y value="${c.y}"></div>
        <div><label>글자 크기 ${c.size}</label><input type="range" min="30" max="90" step="1" data-size value="${c.size}"></div>
      </div>`;
    const q = (sel_) => el.querySelector(sel_);
    q("[data-text]").oninput = (e) => { c.text = e.target.value; drawCapPreview(); };
    q("[data-a]").oninput = (e) => { c.at[0] = parseFloat(e.target.value) || 0; drawCapPreview(); };
    q("[data-b]").oninput = (e) => { c.at[1] = parseFloat(e.target.value) || 0; drawCapPreview(); };
    q("[data-y]").oninput = (e) => {
      c.y = parseFloat(e.target.value);
      e.target.previousElementSibling.textContent = `세로 위치 ${c.y}`; drawCapPreview();
    };
    q("[data-size]").oninput = (e) => {
      c.size = parseInt(e.target.value);
      e.target.previousElementSibling.textContent = `글자 크기 ${c.size}`; drawCapPreview();
    };
    q("[data-now]").onclick = () => {
      const off = trimActive(s) ? s.trim[0] : 0;
      const t = Math.max(0, vid().currentTime - off);
      c.at = [+fmt(t), +fmt(t + (c.at[1] - c.at[0]))];
      renderCaps(); drawCapPreview();
    };
    q("[data-del]").onclick = () => { s.captions.splice(i, 1); renderCaps(); renderClips(); drawCapPreview(); };
    box.appendChild(el);
  });
}

/* ── 업로드 ────────────────────────────────────────────── */
$("pick").onclick = (e) => { e.preventDefault(); $("file").click(); };
$("file").onchange = () => upload($("file").files);

const drop = $("drop");
["dragenter", "dragover"].forEach((e) =>
  drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((e) =>
  drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (ev) => upload(ev.dataTransfer.files));

async function upload(files) {
  if (!files?.length) return;
  const fd = new FormData();
  [...files].forEach((f) => fd.append("files", f));
  drop.textContent = "업로드 중...";
  try {
    await api("/api/upload", { method: "POST", body: fd });
    await loadClips();
  } catch (e) { banner("err", `업로드 실패: ${e.message}`); }
  drop.innerHTML = `영상을 끌어다 놓거나 <a href="#" id="pick">파일 선택</a>`;
  drop.querySelector("#pick").onclick = (e) => { e.preventDefault(); $("file").click(); };
}

/* ── BGM ───────────────────────────────────────────────── */
let styles = [];
async function loadStyles() {
  styles = await api("/api/bgm/styles");
  const sel_ = $("style");
  sel_.innerHTML = styles.map((s) => `<option value="${s.id}">${s.id} (${s.bpm} BPM)</option>`).join("")
    + `<option value="none">음악 없음</option>`;
  sel_.onchange = showDesc; showDesc();
}
function showDesc() {
  const s = styles.find((x) => x.id === $("style").value);
  $("styleDesc").textContent = s ? s.desc : "BGM 없이 현장음만 사용합니다.";
  $("prevBgm").disabled = !s;
}
$("prevBgm").onclick = () => {
  const a = $("bgmAudio");
  a.src = `/api/bgm/preview?style=${$("style").value}`;
  a.play();
};
$("vol").oninput = (e) => $("volLabel").textContent = e.target.value;
$("transition").oninput = (e) => $("trLabel").textContent = e.target.value;

/* ── 렌더링 ────────────────────────────────────────────── */
function buildConfig() {
  const list = chosen();
  return {
    output: $("outName").value.trim() || "output",
    transition: parseFloat($("transition").value),
    music: {
      style: $("style").value,
      volume: parseFloat($("vol").value),
      duck: $("duck").checked,
    },
    audio: { keep_original: $("keepOrig").checked, loudness: -14 },
    clips: list.map((c) => {
      const s = sel.get(c.name);
      const o = { file: `clips/${c.name}` };
      if (trimActive(s)) o.trim = s.trim;
      const caps = s.captions.filter((x) => (x.text || "").trim());
      if (caps.length) o.captions = caps.map((x) => ({
        text: x.text, at: x.at, y: x.y, size: x.size }));
      return o;
    }),
  };
}

$("render").onclick = async () => {
  const cfg = buildConfig();
  if (!cfg.clips.length) { banner("err", "클립을 하나 이상 체크하세요."); return; }
  $("msg").innerHTML = ""; $("result").hidden = true;
  $("log").hidden = false; $("log").textContent = "";
  $("bar").hidden = false; $("render").disabled = true;
  try {
    await api("/api/render", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cfg),
    });
    poll();
  } catch (e) {
    banner("err", e.message); $("bar").hidden = true; $("render").disabled = false;
  }
};

async function poll() {
  const st = await api("/api/render/status");
  $("log").textContent = st.log.join("\n");
  $("log").scrollTop = $("log").scrollHeight;
  if (st.running) { setTimeout(poll, 600); return; }
  $("bar").hidden = true; $("render").disabled = false;
  if (st.ok && st.outputs.length) {
    banner("ok", "렌더링 완료");
    $("result").hidden = false;
    $("outVid").src = `/api/out/${encodeURIComponent(st.outputs[0])}?t=${Date.now()}`;
    $("outList").textContent = st.outputs.join("  ·  ");
  } else {
    banner("err", "렌더링 실패 — 아래 로그를 확인하세요.");
  }
}

$("reveal").onclick = () => api("/api/reveal", { method: "POST" }).catch(() => {});

/* ── 시작 ──────────────────────────────────────────────── */
(async function init() {
  try { await loadEnv(); } catch {}
  await loadStyles();
  await loadClips();
  updateTrimUI();
})();
