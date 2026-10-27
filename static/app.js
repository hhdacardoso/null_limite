// Null Limite web UI: plain JS over the authenticated API (no build step, no external assets)
const STATUS = {
  approved: "aprovado", pending: "pendente", pending_no_file: "pendente (sem arquivo)",
  rejected: "rejeitado", missing: "sem comprovante", expired: "vencida",
};
const FRESHNESS = { atualizado: "atualizado", atencao: "atenção", desatualizado: "desatualizado" };
const TEAMS = { gestao_projetos: "Gestão de Projetos", analise_negocios: "Análise de Negócios", engenharia: "Engenharia" };
const TARGETS = { certificate: "Certificação", project: "Projeto", education: "Formação" };
const LEVELS = { technical: "Técnico", bachelor: "Graduação", specialization: "Especialização", mba: "MBA", master: "Mestrado", doctorate: "Doutorado" };
const SECTORS = ["energia", "financeiro", "seguros", "saude", "governo", "varejo", "telecom", "tecnologia", "industria", "logistica", "educacao", "outros"];

const state = { token: null, user: null, tab: null, bidId: null, evidenceStatus: "pending" };
const app = document.getElementById("app");

function storage(action, value) {
  try {
    if (action === "get") return localStorage.getItem("nl_token");
    if (action === "set") localStorage.setItem("nl_token", value);
    if (action === "clear") localStorage.removeItem("nl_token");
  } catch (e) { /* private mode: session lasts until reload */ }
  return null;
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function pill(status, text) {
  return `<span class="pill ${esc(status)}">${esc(text ?? STATUS[status] ?? FRESHNESS[status] ?? status)}</span>`;
}

function date(value) {
  return value ? new Date(value + "Z").toLocaleDateString("pt-BR") : "";
}

function toast(message, error = false) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.className = error ? "error" : "";
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.hidden = true; }, 3500);
}

async function api(path, { method = "GET", body, form } = {}) {
  const headers = state.token ? { Authorization: `Bearer ${state.token}` } : {};
  let payload;
  if (form) payload = form;
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  const response = await fetch(path, { method, headers, body: payload });
  if (response.status === 401 && state.token) { logout(); throw new Error("Sessão expirada, entre de novo"); }
  if (!response.ok) {
    let detail = response.statusText;
    try { const data = await response.json(); detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail); } catch (e) { /* not JSON */ }
    throw new Error(detail);
  }
  return response.status === 204 ? null : response.json();
}

async function guard(action) {
  try { await action(); } catch (e) { toast(e.message, true); }
}

// Session
function logout() {
  state.token = null; state.user = null; storage("clear");
  renderLogin();
}

function renderLogin() {
  document.getElementById("tabs").innerHTML = "";
  document.getElementById("user").innerHTML = "";
  app.innerHTML = `
    <div class="card login">
      <h1>Entrar</h1>
      <form id="login">
        <label>E-mail <input name="username" type="email" required autocomplete="username"></label>
        <label>Senha <input name="password" type="password" required autocomplete="current-password"></label>
        <button class="btn">Entrar</button>
      </form>
      <p class="hint">Demo: <b>funcionario@nexora.demo</b> (profissional) ou <b>gestor1@nexora.demo</b> (gestor), senha <b>demo123</b>.</p>
    </div>`;
  document.getElementById("login").onsubmit = event => {
    event.preventDefault();
    guard(async () => {
      const response = await fetch("/auth/login", { method: "POST", body: new URLSearchParams(new FormData(event.target)) });
      if (!response.ok) throw new Error("E-mail ou senha inválidos");
      state.token = (await response.json()).access_token;
      storage("set", state.token);
      await start();
    });
  };
}

async function start() {
  state.user = await api("/auth/me");
  const manager = state.user.role === "manager";
  const tabs = manager
    ? [["bids", "Editais"], ["evidences", "Comprovantes"], ["health", "Saúde da base"]]
    : [["profile", "Meu perfil"], ["teams", "Equipes"]];
  document.getElementById("user").innerHTML =
    `<span>${esc(state.user.full_name)} · ${manager ? "gestor" : "profissional"}</span><button class="btn secondary small" id="logout">Sair</button>`;
  document.getElementById("logout").onclick = logout;
  const nav = document.getElementById("tabs");
  nav.innerHTML = tabs.map(([id, label]) => `<button data-tab="${id}">${label}</button>`).join("");
  nav.onclick = event => { if (event.target.dataset.tab) show(event.target.dataset.tab); };
  show(tabs[0][0]);
}

function show(tab) {
  state.tab = tab;
  document.querySelectorAll("#tabs button").forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
  app.innerHTML = `<p class="muted">Carregando...</p>`;
  guard({ bids: renderBids, evidences: renderEvidences, health: renderHealth, profile: renderProfile, teams: renderTeams }[tab]);
}

function refresh() { show(state.tab); }

// Manager: bids, recommended team and ranking
function criteriaTable(criteria) {
  return `<table class="criteria"><tbody>${criteria.map(c => `
    <tr>
      <td><b>${esc(c.description)}</b></td>
      <td class="num">${c.points}/${c.max_points}${c.potential_points > c.points ? ` <small>(pot. ${c.potential_points})</small>` : ""}</td>
      <td>${esc(c.explanation)}
        <div class="items">${c.items.map(i => `<span title="${esc(STATUS[i.status])}">${pill(i.status, i.label)}</span>`).join("")}</div>
      </td>
    </tr>`).join("")}</tbody></table>`;
}

function scoreBar(points, potential, max) {
  const pct = v => max ? Math.min(100, 100 * v / max) : 0;
  return `<div class="bar potential"><div class="pot" style="width:${pct(potential)}%"></div><div class="now" style="width:${pct(points)}%"></div></div>`;
}

async function renderBids() {
  const bids = await api("/bids");
  if (!bids.length) { app.innerHTML = `<div class="card">Nenhum edital cadastrado. Cadastre pelo Swagger em <a href="/docs">/docs</a>.</div>`; return; }
  if (!bids.some(b => b.id === state.bidId)) state.bidId = bids[0].id;
  const [team, ranking] = await Promise.all([api(`/bids/${state.bidId}/team`), api(`/bids/${state.bidId}/ranking?limit=10`)]);
  const bid = bids.find(b => b.id === state.bidId);

  app.innerHTML = `
    <div class="card">
      <div class="row spread">
        <div>
          <label>Edital <select id="bid">${bids.map(b => `<option value="${b.id}" ${b.id === state.bidId ? "selected" : ""}>${esc(b.title)}</option>`).join("")}</select></label>
          ${bid.description ? `<p class="muted">${esc(bid.description)}</p>` : ""}
        </div>
        <div>
          <div class="score">${team.points} <small>/ ${team.max_points} pts</small></div>
          <small>nota técnica comprovada · potencial ${team.potential_points} com os pendentes</small>
          ${scoreBar(team.points, team.potential_points, team.max_points)}
        </div>
      </div>
    </div>

    ${team.pending_gains.length ? `
    <div class="card">
      <h2>Aprovar para subir a nota</h2>
      <p class="muted">Comprovantes pendentes da equipe recomendada que mais aumentam a nota técnica.</p>
      ${team.pending_gains.map(g => `
        <div class="mission">
          <div><b>+${g.gain} pts</b> · ${esc(g.full_name)} (${esc(g.role_name)}): ${esc(g.item)}</div>
          <div class="row">
            <button class="btn secondary small" data-file="${g.evidence_id}">Ver arquivo</button>
            <button class="btn small" data-approve="${g.evidence_id}">Aprovar</button>
          </div>
        </div>`).join("")}
    </div>` : ""}

    <h2>Equipe recomendada</h2>
    <div class="grid">${team.slots.map(slot => `
      <div class="card">
        <small class="muted">${esc(slot.role_name)}</small>
        ${slot.member ? `
          <h3>${esc(slot.member.full_name)}</h3>
          <small>${esc(slot.member.job_title || "")}</small>
          <div class="score">${slot.member.points} <small>/ ${slot.member.max_points}</small></div>
          ${scoreBar(slot.member.points, slot.member.potential_points, slot.member.max_points)}
          <details><summary>Detalhamento por critério</summary>${criteriaTable(slot.member.criteria)}</details>`
        : `<p class="muted">Ninguém disponível para esta vaga.</p>`}
      </div>`).join("")}
    </div>

    <h2>Ranking por vaga</h2>
    ${ranking.map(role => `
      <div class="card">
        <h3>${esc(role.role_name)} <small class="muted">· máximo ${role.max_points} pts</small></h3>
        <div class="table-wrap"><table>
          <thead><tr><th>#</th><th>Profissional</th><th class="num">Comprovado</th><th class="num">Potencial</th><th>Detalhe</th></tr></thead>
          <tbody>${role.candidates.map((c, i) => `
            <tr>
              <td>${i + 1}</td>
              <td>${esc(c.full_name)}<br><small>${esc(c.job_title || "")}</small></td>
              <td class="num"><b>${c.points}</b></td>
              <td class="num">${c.potential_points}</td>
              <td><details><summary>ver</summary>${criteriaTable(c.criteria)}</details></td>
            </tr>`).join("")}
          </tbody>
        </table></div>
      </div>`).join("")}`;

  document.getElementById("bid").onchange = event => { state.bidId = Number(event.target.value); refresh(); };
  bindEvidenceButtons();
}

// Manager: evidence queue
async function renderEvidences() {
  const query = state.evidenceStatus ? `?status=${state.evidenceStatus}` : "";
  const evidences = (await api(`/evidences${query}`)).filter(e => e.professional_id !== state.user.id);
  app.innerHTML = `
    <div class="card">
      <div class="row spread">
        <h2>Comprovantes</h2>
        <label>Status <select id="status">
          ${[["pending", "pendentes"], ["approved", "aprovados"], ["rejected", "rejeitados"], ["", "todos"]]
            .map(([v, l]) => `<option value="${v}" ${v === state.evidenceStatus ? "selected" : ""}>${l}</option>`).join("")}
        </select></label>
      </div>
      <p class="muted">${evidences.length} comprovante(s). Os mais recentes aparecem primeiro. Formação sem arquivo é um pedido de validação: confira por outro meio (diploma, consulta à instituição) antes de aprovar.</p>
      <div class="table-wrap"><table>
        <thead><tr><th>Profissional</th><th>Item</th><th>Arquivo</th><th>Enviado</th><th>Status</th><th></th></tr></thead>
        <tbody>${evidences.reverse().map(e => `
          <tr>
            <td>${esc(e.professional_name)}</td>
            <td><small>${esc(TARGETS[e.certificate_id ? "certificate" : e.project_id ? "project" : "education"])}</small><br>${esc(e.item_label)}</td>
            <td>${e.filename ? `<button class="btn secondary small" data-file="${e.id}">${esc(e.filename)}</button>` : `<span class="muted">sem arquivo</span>`}</td>
            <td>${date(e.created_at)}</td>
            <td>${pill(e.status === "pending" && !e.filename ? "pending_no_file" : e.status)}${e.review_note ? `<br><small>${esc(e.review_note)}</small>` : ""}</td>
            <td class="row">${e.status === "pending" ? `
              <button class="btn small" data-approve="${e.id}">Aprovar</button>
              <button class="btn danger small" data-reject="${e.id}">Rejeitar</button>` : ""}
            </td>
          </tr>`).join("")}
        </tbody>
      </table></div>
    </div>`;
  document.getElementById("status").onchange = event => { state.evidenceStatus = event.target.value; refresh(); };
  bindEvidenceButtons();
}

function bindEvidenceButtons() {
  app.querySelectorAll("[data-approve]").forEach(b => b.onclick = () => review(b.dataset.approve, "approved"));
  app.querySelectorAll("[data-reject]").forEach(b => b.onclick = () => {
    const note = prompt("Motivo da rejeição (o profissional vai ver):");
    if (note !== null) review(b.dataset.reject, "rejected", note);
  });
  app.querySelectorAll("[data-file]").forEach(b => b.onclick = () => openFile(b.dataset.file));
}

function review(id, status, note) {
  guard(async () => {
    await api(`/evidences/${id}/review`, { method: "POST", body: { status, note: note || null } });
    toast(status === "approved" ? "Comprovante aprovado" : "Comprovante rejeitado");
    refresh();
  });
}

function openFile(id) {
  guard(async () => {
    const response = await fetch(`/evidences/${id}/file`, { headers: { Authorization: `Bearer ${state.token}` } });
    if (!response.ok) throw new Error("Arquivo indisponível");
    const url = URL.createObjectURL(await response.blob());
    window.open(url, "_blank");
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  });
}

// Manager: base health
async function renderHealth() {
  const [overview, teams, board] = await Promise.all([
    api("/gamification/overview"), api("/leaderboard/teams"), api("/leaderboard/professionals?limit=10"),
  ]);
  app.innerHTML = `
    <div class="tiles">
      <div class="tile"><b>${overview.updated_pct}%</b>perfis atualizados (12 meses)</div>
      <div class="tile"><b>${overview.attention}</b>em atenção (12 a 24 meses)</div>
      <div class="tile"><b>${overview.stale}</b>desatualizados (mais de 24 meses)</div>
      <div class="tile"><b>${overview.proven_items_pct}%</b>itens comprovados</div>
      <div class="tile"><b>${overview.pending_evidences}</b>comprovantes na fila (${overview.pending_without_file} sem arquivo)</div>
    </div>

    <div class="card">
      <h2>Quem cobrar primeiro</h2>
      <p class="muted">Perfis não atualizados, ordenados pelo potencial nos editais abertos.</p>
      <div class="table-wrap"><table>
        <thead><tr><th>Profissional</th><th>Equipe</th><th>Status</th><th class="num">Sem atualizar</th><th class="num">Potencial em editais</th></tr></thead>
        <tbody>${overview.stale_profiles.slice(0, 15).map(s => `
          <tr><td>${esc(s.full_name)}</td><td>${esc(TEAMS[s.team] || "")}</td><td>${pill(s.freshness)}</td>
          <td class="num">${Math.round(s.days_since_update / 30)} meses</td><td class="num">${s.bid_potential_points} pts</td></tr>`).join("")}
        </tbody>
      </table></div>
    </div>

    <div class="grid">
      <div class="card">
        <h2>Ranking por equipe</h2>
        ${teamTable(teams)}
      </div>
      <div class="card">
        <h2>Top 10 profissionais</h2>
        <table><thead><tr><th>#</th><th>Profissional</th><th class="num">Pontos</th><th>Nível</th></tr></thead>
        <tbody>${board.map(p => `<tr><td>${p.position}</td><td>${esc(p.full_name)}<br><small>${esc(TEAMS[p.team] || "")}</small></td>
          <td class="num">${p.points}</td><td>${esc(p.level)} ${pill(p.freshness)}</td></tr>`).join("")}</tbody></table>
      </div>
    </div>`;
}

function teamTable(teams) {
  return `<table><thead><tr><th>Equipe</th><th class="num">Pessoas</th><th class="num">Média</th><th class="num">Atualizados</th><th class="num">Comprovados</th></tr></thead>
    <tbody>${teams.map(t => `<tr><td>${esc(TEAMS[t.team] || "Sem equipe")}</td><td class="num">${t.members}</td><td class="num">${t.avg_points}</td>
      <td class="num">${t.updated_pct}%</td><td class="num">${t.proven_items_pct}%</td></tr>`).join("")}</tbody></table>`;
}

// Professional: own profile, missions and uploads
async function renderProfile() {
  const me = await api("/professionals/me/gamification");
  const nextLevel = me.next_level
    ? `<small>${me.points_to_next_level} pts para ${esc(me.next_level)}</small><div class="bar"><div style="width:${Math.max(4, 100 - 100 * me.points_to_next_level / (me.points + me.points_to_next_level))}%"></div></div>`
    : `<small>Nível máximo</small>`;

  app.innerHTML = `
    <div class="grid">
      <div class="card">
        <small class="muted">Seus pontos</small>
        <div class="score">${me.points} <small>pts · ${esc(me.level)}</small></div>
        ${nextLevel}
        <small>${me.items_points} pelos itens + ${me.freshness_bonus} de bônus de atualização</small>
      </div>
      <div class="card">
        <small class="muted">Atualização do perfil</small>
        <h3>${pill(me.freshness)} há ${Math.round(me.days_since_update / 30)} mese(s)</h3>
        <small>Posição ${me.position} de ${me.total_professionals} (top ${me.top_percent}%)${me.team_position ? ` · ${me.team_position}º de ${me.team_size} na sua equipe` : ""}</small>
      </div>
      <div class="card">
        <small class="muted">Badges</small>
        <div class="items">${me.badges.length ? me.badges.map(b => `<span class="pill badge" title="${esc(b.description)}">${esc(b.name)}</span>`).join("") : `<span class="muted">Nenhum ainda</span>`}</div>
      </div>
    </div>

    <div class="card">
      <h2>Missões</h2>
      <p class="muted">O que mais vale a pena comprovar agora. O impacto em editais é quanto você passa a somar na nota técnica de uma proposta.</p>
      ${me.missions.length ? me.missions.map(missionRow).join("") : `<p>Tudo comprovado. Bom trabalho!</p>`}
    </div>

    <div class="card">
      <h2>Meus itens</h2>
      <div class="table-wrap"><table>
        <thead><tr><th>Tipo</th><th>Item</th><th>Status</th><th class="num">Pontos</th><th></th></tr></thead>
        <tbody>${me.items.map(i => `
          <tr><td>${esc(TARGETS[i.type])}</td><td>${esc(i.label)}</td><td>${pill(i.status)}</td><td class="num">${i.points}</td>
          <td>${["missing", "rejected", "pending_no_file"].includes(i.status) ? uploadButtons(i.type, i.id) : ""}</td></tr>`).join("")}
        </tbody>
      </table></div>
    </div>

    <div class="card">
      <h2>Adicionar ao perfil</h2>
      <p class="muted">Cadastre o item e depois envie o comprovante na lista acima. Só item comprovado pontua nos editais.</p>
      <details><summary>Nova certificação</summary>
        <form class="inline" data-add="certificates">
          <label>Nome <input name="name" required placeholder="ex.: PMP"></label>
          <label>Emissor <input name="issuer" placeholder="ex.: PMI"></label>
          <label>Emissão <input name="issue_date" type="date"></label>
          <label>Validade <input name="expiry_date" type="date"></label>
          <button class="btn">Adicionar</button>
        </form>
      </details>
      <details><summary>Novo curso / formação</summary>
        <form class="inline" data-add="education">
          <label>Nível <select name="level">${Object.entries(LEVELS).map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select></label>
          <label>Curso <input name="course" required></label>
          <label>Instituição <input name="institution"></label>
          <label>Conclusão <input name="completion_date" type="date"></label>
          <button class="btn">Adicionar</button>
        </form>
      </details>
      <details><summary>Novo projeto</summary>
        <form class="inline" data-add="projects">
          <label>Projeto <input name="name" required></label>
          <label>Cliente <input name="client"></label>
          <label>Setor <select name="sector">${SECTORS.map(s => `<option>${s}</option>`).join("")}</select></label>
          <label>Papel <input name="role"></label>
          <label>Início <input name="start_date" type="date"></label>
          <label>Fim <input name="end_date" type="date"></label>
          <button class="btn">Adicionar</button>
        </form>
      </details>
    </div>
    <input type="file" id="file-picker" accept=".pdf,.png,.jpg,.jpeg" hidden>`;

  bindProfile();
}

function uploadButtons(type, id) {
  return `<div class="row">
    <button class="btn small" data-upload="${type}:${id}">Enviar comprovante</button>
    ${type === "education" ? `<button class="btn secondary small" data-nofile="${id}">Pedir validação sem arquivo</button>` : ""}
  </div>`;
}

function missionRow(m) {
  const impact = m.bid_impact.map(i => `+${i.gain} pts em ${esc(i.role_name)} (${esc(i.bid_title)})`).join(" · ");
  const actionable = m.item_id && m.action !== "renew_certificate";
  return `<div class="mission">
    <div>
      <b>${esc(m.title)}</b> <span class="pill badge">+${m.profile_points} pts no perfil</span>
      ${impact ? `<div class="impact">Impacto em editais: ${impact}</div>` : ""}
    </div>
    ${actionable ? uploadButtons(m.item_type, m.item_id) : ""}
  </div>`;
}

function bindProfile() {
  const picker = document.getElementById("file-picker");
  app.querySelectorAll("[data-upload]").forEach(b => b.onclick = () => {
    picker.dataset.target = b.dataset.upload;
    picker.value = "";
    picker.click();
  });
  picker.onchange = () => {
    if (!picker.files.length) return;
    const [type, id] = picker.dataset.target.split(":");
    const form = new FormData();
    form.append("target_type", type);
    form.append("target_id", id);
    form.append("file", picker.files[0]);
    sendEvidence(form, "Comprovante enviado: aguardando validação do gestor");
  };
  app.querySelectorAll("[data-nofile]").forEach(b => b.onclick = () => {
    const form = new FormData();
    form.append("target_type", "education");
    form.append("target_id", b.dataset.nofile);
    sendEvidence(form, "Pedido de validação enviado ao gestor");
  });
  app.querySelectorAll("form[data-add]").forEach(form => form.onsubmit = event => {
    event.preventDefault();
    const body = {};
    for (const [key, value] of new FormData(form)) {
      if (value) body[key] = key.endsWith("_date") ? `${value}T00:00:00` : value;
    }
    guard(async () => {
      await api(`/professionals/me/${form.dataset.add}`, { method: "POST", body });
      toast("Item adicionado. Agora envie o comprovante.");
      refresh();
    });
  });
}

function sendEvidence(form, message) {
  guard(async () => {
    await api("/professionals/me/evidences", { method: "POST", form });
    toast(message);
    refresh();
  });
}

// Professional: team ranking (aggregates only)
async function renderTeams() {
  const teams = await api("/leaderboard/teams");
  app.innerHTML = `<div class="card"><h2>Ranking por equipe</h2>
    <p class="muted">Média de pontos e quanto de cada equipe está atualizado e comprovado. Pontos individuais dos colegas não são exibidos.</p>
    ${teamTable(teams)}</div>`;
}

// Boot
state.token = storage("get");
if (state.token) guard(start); else renderLogin();
