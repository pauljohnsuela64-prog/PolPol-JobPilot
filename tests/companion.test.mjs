import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {controls, inspectOrFill, matchField, targetUrl} from '../extension/mapping.mjs';
import {fill, preview} from '../extension/handoff.mjs';

const email = {key: 'email', label: 'Email', status: 'verified', value: 'demo@example.org'};

test('manifest limits page access to user gestures and pins the companion origin', () => {
  const manifest = JSON.parse(readFileSync(new URL('../extension/manifest.json', import.meta.url)));
  assert.deepEqual(manifest.permissions, ['activeTab', 'scripting']);
  for (const key of ['host_permissions', 'content_scripts', 'background', 'externally_connectable', 'web_accessible_resources']) {
    assert.equal(manifest[key], undefined);
  }
  const hex = createHash('sha256').update(Buffer.from(manifest.key, 'base64')).digest('hex').slice(0, 32);
  const id = [...hex].map(c => String.fromCharCode(97 + parseInt(c, 16))).join('');
  assert.match(readFileSync(new URL('../backend/assistant_routes.py', import.meta.url), 'utf8'), new RegExp('chrome-extension://' + id));
});

test('only reviewed, unambiguous mappings can be selected', () => {
  assert.equal(matchField(email).id, 'email');
  for (const changed of [{label: 'Salary'}, {key: 'phone'}, {status: 'unsupported'}, {value: 'Needs your input.'}]) {
    assert.equal(matchField({...email, ...changed}), undefined);
  }
  const answer = {key: 'screening_2', label: 'Describe a relevant project.', value: 'Built SQL reports.', status: 'reviewed'};
  assert.equal(matchField(answer).id, 'project');
  assert.equal(matchField({...answer, label: 'Expected pay?'}), undefined);
});

test('wrong domain or navigation prevents requesting or sending package data', async () => {
  const original = globalThis.fetch;
  let tab = {id: 1, url: targetUrl + '?other'};
  globalThis.chrome = {tabs: {query: async () => [tab]}};
  globalThis.fetch = () => assert.fail('Unexpected API request');
  try {
    await assert.rejects(preview('synthetic-token'), /toolbar extension/);
    tab = {id: 2, url: targetUrl};
    await assert.rejects(fill({tabId: 1, token: 'synthetic-token', fields: [{...email, controlId: 'email'}]}, ['email']), /toolbar extension/);
  } finally {
    globalThis.fetch = original;
    delete globalThis.chrome;
  }
});

test('document and element identity are rechecked before writing any reviewed value', () => {
  const form = {hasAttribute: () => false};
  class Input {
    type = 'email'; name = 'email'; form = form; value = ''; maxLength = -1;
    labels = [{htmlFor: 'email', textContent: 'Email'}];
    attrs = ['id', 'name', 'type'];
    getAttributeNames() { return this.attrs; }
    closest() { return null; }
    matches() { return false; }
    checkVisibility() { return true; }
  }
  globalThis.window = {top: null};
  window.top = window;
  globalThis.location = {href: targetUrl};
  globalThis.HTMLInputElement = Input;
  globalThis.HTMLTextAreaElement = class {};
  let element = new Input();
  globalThis.document = {
    visibilityState: 'visible', forms: [form],
    querySelector: selector => selector.startsWith('form#') ? form : null,
    querySelectorAll: selector => selector === '[id="email"]' ? [element] : [],
  };
  const inspect = () => inspectOrFill(targetUrl, controls, null, 'nonce');
  const apply = () => inspectOrFill(targetUrl, controls, [{id: 'email', value: email.value}], 'nonce');
  try {
    assert.deepEqual(inspect().available, {email: true});
    element = new Input();
    assert.ok(apply().error);
    inspect(); element.attrs.push('autocomplete'); assert.ok(apply().error);
    element.attrs.pop(); inspect(); element.value = 'Already entered'; assert.ok(apply().error);
    assert.equal(element.value, 'Already entered');
    element.value = ''; inspect(); location.href += '#changed'; assert.ok(apply().error);
    location.href = targetUrl; inspect(); assert.deepEqual(apply().filled, ['email']);
    assert.equal(element.value, email.value);
  } finally {
    for (const key of ['window','document','location','HTMLInputElement','HTMLTextAreaElement','jobPilotPreview']) delete globalThis[key];
  }
});

test('fill passes only the confirmed value to the previewed document', async () => {
  const original = globalThis.fetch;
  const phone = {key: 'phone', label: 'Phone', value: '2025550123', status: 'verified'};
  const review = {tabId: 10, documentId: 'doc', nonce: 'nonce', token: 'private-token',
    fields: [{...email, controlId: 'email'}, {...phone, controlId: 'phone'}]};
  globalThis.fetch = async (url, options) => {
    assert.equal(url, 'http://127.0.0.1:8000/api/assistant/extension/claim');
    assert.equal(options.credentials, 'omit');
    assert.equal(options.redirect, 'error');
    assert.deepEqual(JSON.parse(options.body), {token: 'private-token', target_url: targetUrl, field_keys: ['email']});
    return {ok: true, json: async () => ({fields: [email], receipt: 'private-receipt'})};
  };
  globalThis.chrome = {tabs: {query: async () => [{id: 10, url: targetUrl}]}, scripting: {
    executeScript: async injection => {
      assert.deepEqual(injection.target, {tabId: 10, documentIds: ['doc']});
      assert.equal(injection.world, 'ISOLATED');
      assert.deepEqual(injection.args, [targetUrl, controls, [{id: 'email', value: email.value}], 'nonce']);
      return [{frameId: 0, documentId: 'doc', result: {filled: ['email']}}];
    },
  }};
  try {
    const result = await fill(review, ['email']);
    assert.deepEqual(result.filled, ['email']);
    assert.equal(result.problem, undefined);
    await assert.rejects(fill(review, ['email']), /Select reviewed fields/);
  } finally {
    globalThis.fetch = original;
    delete globalThis.chrome;
  }
});
