export const targetUrl = 'http://127.0.0.1:4179/application.html';

export const controls = [
  {id: 'full_name', label: 'Full name', type: 'text'},
  {id: 'email', label: 'Email', type: 'email'},
  {id: 'phone', label: 'Phone', type: 'tel'},
  {id: 'experience', label: 'Experience', type: 'textarea'},
  {id: 'education', label: 'Education', type: 'textarea'},
  {id: 'skills', label: 'What are your relevant skills?', type: 'textarea', screening: true},
  {id: 'project', label: 'Describe a relevant project.', type: 'textarea', screening: true},
];

export function matchField(field) {
  if (!field || typeof field.value !== 'string' || !field.value.trim() ||
      /^needs your input\.?$/i.test(field.value.trim())) return;
  return controls.find(control => control.label === field.label && (control.screening
    ? /^screening_[1-9]\d*$/.test(field.key) && field.status === 'reviewed'
    : control.id === field.key && field.status === 'verified'));
}

// Chrome serializes this function into the top frame's isolated world. Only public
// descriptors enter on inspection. On fill, entries contain the selected values.
export function inspectOrFill(url, specs, entries, nonce) {
  const form = document.querySelector('form#jobpilot-practice-v1');
  if (window.top !== window || location.href !== url || document.visibilityState !== 'visible' ||
      document.forms.length !== 1 || !form || form.hasAttribute('action') ||
      document.querySelector('script, iframe, frame, base, object, embed')) return {error: 'Open the original practice form.'};

  const available = {};
  const elements = new Map();
  for (const spec of specs) {
    const matches = document.querySelectorAll('[id="' + spec.id + '"]');
    const el = matches[0];
    const correctType = spec.type === 'textarea' ? el instanceof HTMLTextAreaElement :
      el instanceof HTMLInputElement && el.type === spec.type;
    if (matches.length !== 1 || !correctType || el.form !== form || el.name !== spec.id ||
        el.getAttributeNames().some(attr => !['id', 'name', 'type'].includes(attr)) ||
        el.labels.length !== 1 || el.labels[0].htmlFor !== spec.id ||
        el.labels[0].textContent.trim() !== spec.label || el.closest('[inert], [hidden], [aria-hidden="true"]') ||
        !el.checkVisibility({opacityProperty: true, visibilityProperty: true}) ||
        el.matches(':disabled') || el.readOnly || el.value !== '') continue;
    available[spec.id] = true;
    elements.set(spec.id, el);
  }
  if (!entries) {
    globalThis.jobPilotPreview = {nonce, elements, form};
    return {available};
  }
  const preview = globalThis.jobPilotPreview;
  delete globalThis.jobPilotPreview;
  if (preview?.nonce !== nonce || preview.form !== form || entries.some(({id, value}) =>
    !elements.has(id) || preview.elements.get(id) !== elements.get(id) ||
    (elements.get(id).maxLength >= 0 && value.length > elements.get(id).maxLength))) {
    return {error: 'The form changed. Review a fresh package.'};
  }
  const filled = [];
  for (const {id, value} of entries) {
    const el = elements.get(id);
    el.value = value;
    if (el.value === value) filled.push(id);
  }
  return {filled};
}
