'use strict';
let dirty = false;
document.querySelectorAll('[data-dirty]').forEach(form => {
  form.addEventListener('input', () => { dirty = true; });
  form.addEventListener('submit', () => { dirty = false; });
});
const dialog = document.getElementById('confirm-dialog');
let pendingForm = null, pendingButton = null;
document.querySelectorAll('form[data-confirm]').forEach(form => {
  form.addEventListener('submit', event => {
    if (form.dataset.confirmed === 'yes') return;
    event.preventDefault();
    pendingForm = form; pendingButton = event.submitter;
    document.getElementById('confirm-message').textContent = form.dataset.confirm;
    const preview = document.getElementById('confirm-preview');
    preview.replaceChildren();
    const source = form.dataset.preview && document.getElementById(form.dataset.preview);
    if (source) { const clone = source.cloneNode(true); clone.removeAttribute('id'); preview.append(clone); }
    dialog.showModal();
  });
});
dialog?.addEventListener('close', () => {
  if (dialog.returnValue === 'confirm' && pendingForm) {
    pendingForm.dataset.confirmed = 'yes';
    pendingForm.requestSubmit(pendingButton);
  }
  pendingForm = pendingButton = null;
});
document.querySelectorAll('[data-copy]').forEach(button => button.addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(document.getElementById(button.dataset.copy).textContent.trim());
    button.textContent = 'Скопировано ✓';
  } catch { button.textContent = 'Выдели и скопируй номер'; }
}));
document.querySelectorAll('[data-back]').forEach(button => button.addEventListener('click', () => {
  if (history.length > 1) history.back(); else location.assign('/');
}));
function countdowns() {
  document.querySelectorAll('[data-countdown]').forEach(el => {
    const remaining = Math.max(0, Date.parse(el.dataset.countdown) - Date.now());
    const seconds = Math.floor(remaining / 1000);
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor(seconds % 86400 / 3600).toString().padStart(2, '0');
    const minutes = Math.floor(seconds % 3600 / 60).toString().padStart(2, '0');
    const secs = (seconds % 60).toString().padStart(2, '0');
    el.textContent = `${days ? days + 'д ' : ''}${hours}:${minutes}:${secs}`;
  });
}
countdowns(); setInterval(countdowns, 1000);
const poll = document.querySelector('[data-poll]');
let previous = null;
async function refreshState() {
  if (!poll || document.hidden) return;
  try {
    const response = await fetch(poll.dataset.poll, {credentials: 'same-origin', cache: 'no-store', headers: {'Accept':'application/json'}});
    if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) return;
    const payload = await response.json();
    const state = JSON.stringify(payload);
    const initial = document.body.dataset;
    const initialChanged = previous === null && (
      payload.status !== initial.contestStatus || String(payload.results) !== initial.resultsPublished ||
      (initial.trackParticipation === 'yes' && (payload.participation || '') !== initial.participation)
    );
    if (initialChanged && !dirty) { location.reload(); return; }
    if (previous !== null && previous !== state && !dirty) location.reload();
    if (!dirty) previous = state;
  } catch { /* The rendered page remains usable while offline. */ }
}
if (poll) { refreshState(); setInterval(refreshState, 8000); }
document.querySelectorAll('[data-score-form]').forEach(form => {
  const update = () => { form.querySelector('[data-total]').textContent = [...form.querySelectorAll('[data-score]')].reduce((total, el) => total + (Number(el.value) || 0), 0); };
  form.addEventListener('input', update); update();
});
document.querySelectorAll('input[type=file][data-max-mb]').forEach(input => input.addEventListener('change', () => {
  input.setCustomValidity('');
  const file = input.files[0];
  if (file && file.size > Number(input.dataset.maxMb) * 1024 * 1024) {
    input.setCustomValidity(`Файл слишком большой. Максимум ${input.dataset.maxMb} MB.`); input.reportValidity();
  }
}));
window.addEventListener('beforeunload', e => { if (dirty) { e.preventDefault(); e.returnValue = ''; } });
