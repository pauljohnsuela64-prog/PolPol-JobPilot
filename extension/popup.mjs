import {fill, post, preview} from './handoff.mjs';

const el = id => document.getElementById(id);
let review, pending;
const checked = () => [...el('fields').querySelectorAll('input:checked')].map(input => input.value);
el('fields').onchange = () => { el('fill').disabled = !checked().length; };

function render(field) {
  const row = document.createElement('div');
  row.className = 'field';
  const label = document.createElement('label');
  const box = document.createElement('input');
  box.type = 'checkbox';
  box.value = field.key;
  box.disabled = !field.controlId;
  label.append(box, ' ' + field.label);
  const value = document.createElement('p');
  value.className = 'value';
  value.textContent = field.status === 'unsupported' ? 'Needs your input.' : field.value;
  const source = document.createElement('small');
  source.textContent = field.controlId ? field.status + ' · ' + field.source :
    'Manual only · Missing value, unsupported mapping, or field already filled.';
  row.append(label, value, source);
  return row;
}

el('authorize').onsubmit = async event => {
  event.preventDefault();
  const button = event.currentTarget.querySelector('button');
  button.disabled = true;
  el('status').textContent = 'Checking the active form and reviewed package…';
  try {
    review = await preview(el('code').value.trim());
    el('code').value = '';
    el('authorize').hidden = true;
    el('opening').textContent = review.title + ' · ' + review.company;
    el('fields').replaceChildren(...review.fields.map(render));
    el('review').hidden = false;
    el('status').textContent = '';
  } catch (error) { el('status').textContent = error.message; }
  finally { button.disabled = false; }
};

async function record() {
  el('retry').hidden = true;
  try {
    await post('complete', {receipt: pending.receipt, filled_keys: pending.filled});
    el('status').textContent = pending.problem?.message ||
      'Filled ' + pending.filled.length + ' field(s). Review the practice form.';
    pending = null;
  } catch {
    el('status').textContent = 'Filled ' + pending.filled.length + ' field(s). Activity recording was interrupted.';
    el('retry').hidden = false;
  }
}
el('retry').onclick = record;
el('fill').onclick = async () => {
  const keys = checked();
  el('review').hidden = true;
  el('fill').disabled = true;
  el('status').textContent = 'Rechecking approvals and selected fields…';
  try { pending = await fill(review, keys); await record(); }
  catch (error) { el('status').textContent = error.message; }
  finally { review = null; el('fields').replaceChildren(); }
};
