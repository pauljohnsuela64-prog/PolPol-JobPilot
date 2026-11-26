import {controls, inspectOrFill, matchField, targetUrl} from './mapping.mjs';

export async function post(action, body) {
  const response = await fetch('http://127.0.0.1:8000/api/assistant/extension/' + action, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
    mode: 'cors', credentials: 'omit', redirect: 'error', referrerPolicy: 'no-referrer',
    cache: 'no-store', signal: AbortSignal.timeout(10000),
  });
  if (!response.ok) throw Error(response.status === 409 || response.status === 410 || response.status === 403
    ? 'Authorization expired or changed. Return to JobPilot and review a new package.'
    : 'JobPilot could not authorize this request. Check the local API and code.');
  return response.json();
}

async function currentTab(expectedId) {
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  if (!tab || tab.url !== targetUrl || (expectedId !== undefined && tab.id !== expectedId)) {
    throw Error('Open the bundled practice application and invoke the toolbar extension on that tab.');
  }
  return tab.id;
}

async function inspect(tabId, documentId, nonce, entries) {
  const target = documentId ? {tabId, documentIds: [documentId]} : {tabId, frameIds: [0]};
  const [res] = await chrome.scripting.executeScript({
    target, world: 'ISOLATED', func: inspectOrFill, args: [targetUrl, controls, entries, nonce],
  });
  if (!res?.documentId || res.frameId !== 0 || !res.result || res.result.error) {
    throw Error(res?.result?.error || 'The practice document is no longer available.');
  }
  return res;
}

export async function preview(token) {
  const tabId = await currentTab();
  const nonce = crypto.randomUUID();
  const inspected = await inspect(tabId, null, nonce, null);
  const packet = await post('preview', {token, target_url: targetUrl});
  const fields = packet.fields.map(field => {
    const control = matchField(field);
    return {...field, controlId: control && inspected.result.available[control.id] ? control.id : null};
  });
  // Ambiguous mappings never reach the fill action.
  for (const field of fields) {
    if (field.controlId && fields.filter(other => other.label === field.label || other.key === field.key).length > 1) {
      field.controlId = null;
    }
  }
  for (const control of controls) {
    if (!fields.some(field => field.label === control.label)) {
      fields.push({key: control.id, label: control.label, value: 'Needs your input.',
        status: 'unsupported', source: 'No corresponding reviewed value', controlId: null});
    }
  }
  return {tabId, nonce, documentId: inspected.documentId, token, title: packet.title, company: packet.company, fields};
}

export async function fill(review, keys) {
  const selected = review.fields.filter(field => field.controlId && keys.includes(field.key));
  if (!selected.length || selected.length !== keys.length || !review.token) throw Error('Select reviewed fields first.');
  await currentTab(review.tabId);
  const token = review.token;
  review.token = null;
  const claimed = await post('claim', {token, target_url: targetUrl, field_keys: keys});
  let filled = [];
  let problem;
  try {
    if (claimed.fields.length !== selected.length || !selected.every(field => claimed.fields.some(other =>
      other.key === field.key && matchField(other)?.id === field.controlId && other.value === field.value))) {
      throw Error('Package changed. Review a fresh package.');
    }
    await currentTab(review.tabId);
    const entries = selected.map(field => ({id: field.controlId, value: field.value}));
    const res = await inspect(review.tabId, review.documentId, review.nonce, entries);
    filled = selected.filter(field => res.result.filled.includes(field.controlId)).map(field => field.key);
  } catch (error) { problem = error; }
  return {filled, problem, receipt: claimed.receipt};
}
