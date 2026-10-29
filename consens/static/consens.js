// Stäng öppna filterpaneler vid klick utanför (details/summary sköter resten).
document.addEventListener("click", e => {
  document.querySelectorAll("details.multiselect[open]").forEach(d => {
    if (!d.contains(e.target)) d.removeAttribute("open");
  });
});

// Sökruta i filterpaneler: filtrera synliga alternativ medan man skriver.
document.querySelectorAll(".ms-panel .ms-sok").forEach(sok => {
  sok.addEventListener("input", () => {
    const q = sok.value.toLowerCase();
    const lista = sok.closest(".ms-panel").querySelector(".lista");
    lista.querySelectorAll("label:not(.ms-grupp-rad label)").forEach(l => {
      l.style.display = l.textContent.toLowerCase().includes(q) ? "" : "none";
    });
    lista.querySelectorAll(".ms-grupp").forEach(g => {
      const namn = g.querySelector(".ms-grupp-namn").textContent.toLowerCase();
      const barn = [...g.querySelectorAll(".ms-grupp-barn label")];
      if (namn.includes(q)) barn.forEach(b => { b.style.display = ""; });
      const traff = namn.includes(q) || barn.some(b => b.style.display !== "none");
      g.style.display = traff ? "" : "none";
      if (q) { if (traff) g.setAttribute("open", ""); }
      else g.toggleAttribute("open",
        barn.some(b => b.querySelector("input").checked));
    });
  });
});

// Affärsregel: Typ-valet styr vilka tidsfilter som visas.
const typVal = document.querySelectorAll("#flodesfilter .typ-val");
function uppdateraTidsfilter() {
  const u = document.querySelector('#flodesfilter .typ-val[value="upphandling"]');
  const a = document.querySelector('#flodesfilter .typ-val[value="avtalsutgang"]');
  if (!u || !a) return;
  const dl = document.getElementById("filter-deadline");
  const ut = document.getElementById("filter-utgang");
  const bada = (u.checked && a.checked) || (!u.checked && !a.checked);
  dl.hidden = !(bada || u.checked);
  ut.hidden = !(bada || a.checked);
  // Rensa val i dolda filter så de inte skickas med
  (dl.hidden ? dl : {querySelectorAll:()=>[]}).querySelectorAll?.("input:checked").forEach(i => i.checked = false);
  (ut.hidden ? ut : {querySelectorAll:()=>[]}).querySelectorAll?.("input:checked").forEach(i => i.checked = false);
}
typVal.forEach(t => t.addEventListener("change", uppdateraTidsfilter));

// Gruppval i Tjänst-panelen: kryssa kategorin -> alla underliggande.
// Kryssrutan i summaryn ska inte fälla in/ut gruppen, bara välja.
document.querySelectorAll(".grupp-val").forEach(gv => {
  const grupp = gv.closest(".ms-grupp");
  const barn = () => grupp.querySelectorAll('.ms-grupp-barn input[name]');
  const antalEl = grupp.querySelector(".ms-grupp-antal");
  gv.addEventListener("click", e => e.stopPropagation());
  gv.addEventListener("change", () => {
    barn().forEach(b => { b.checked = gv.checked; });
    synka();
  });
  const synka = () => {
    const alla = [...barn()];
    const valda = alla.filter(b => b.checked).length;
    gv.checked = valda === alla.length && alla.length > 0;
    gv.indeterminate = valda > 0 && valda < alla.length;
    if (antalEl) antalEl.textContent = valda ? valda + " valda" : "";
  };
  barn().forEach(b => b.addEventListener("change", synka));
  synka();
});

// Läs mer / Visa mindre - rubriken och knappen togglar samma panel.
// Första öppningen loggas för statistiken (fire-and-forget).
const klickade = new Set();
function vaxlaDetalj(mal, klickUrl) {
  const detalj = document.getElementById(mal);
  if (!detalj) return;
  const oppnas = detalj.hidden;
  detalj.hidden = !oppnas;
  const knapp = document.querySelector(`.las-mer[data-mal="${mal}"]`);
  if (knapp) {
    knapp.textContent = oppnas ? "Visa mindre" : "Läs mer";
    knapp.setAttribute("aria-expanded", oppnas ? "true" : "false");
  }
  if (oppnas && klickUrl && !klickade.has(mal)) {
    klickade.add(mal);
    fetch(klickUrl, {keepalive: true}).catch(() => {});
  }
}
document.querySelectorAll(".las-mer").forEach(k => {
  k.addEventListener("click", () => vaxlaDetalj(k.dataset.mal,
    document.querySelector(`.titel-toggle[data-mal="${k.dataset.mal}"]`)?.dataset.klick));
});
document.querySelectorAll(".titel-toggle").forEach(t => {
  t.addEventListener("click", () => vaxlaDetalj(t.dataset.mal, t.dataset.klick));
});

// Live-räknare: bubblan markeras och räknar direkt vid kryss -
// själva sökningen sker fortfarande först vid Tillämpa.
document.querySelectorAll("#flodesfilter details.multiselect").forEach(d => {
  const summary = d.querySelector("summary.ms-toggle");
  if (!summary) return;
  d.addEventListener("change", e => {
    if (!e.target.matches('input[type="checkbox"][name]')) return;
    const antal = d.querySelectorAll('input[type="checkbox"][name]:checked').length;
    let bricka = summary.querySelector(".antal");
    if (antal > 0) {
      if (!bricka) {
        bricka = document.createElement("span");
        bricka.className = "antal";
        summary.insertBefore(bricka, summary.querySelector(".pil"));
        summary.insertBefore(document.createTextNode(" "), summary.querySelector(".pil"));
      }
      bricka.textContent = antal;
      summary.classList.add("aktiv");
    } else {
      if (bricka) bricka.remove();
      summary.classList.remove("aktiv");
    }
  });
});
