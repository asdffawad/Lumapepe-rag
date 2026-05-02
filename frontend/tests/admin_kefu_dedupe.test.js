const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const adminHtml = fs.readFileSync(path.join(__dirname, '..', 'admin.html'), 'utf8');
const match = adminHtml.match(/function appendKefuChatMessage\(message\) \{[\s\S]*?\n\}\n\nfunction handleKefuAdminEvent/);

assert(match, 'appendKefuChatMessage function not found');

const appendFunctionSource = match[0].replace(/\n\nfunction handleKefuAdminEvent$/, '');
const inserted = [];
const box = {
  children: [],
  scrollTop: 0,
  scrollHeight: 100,
  querySelectorAll() {
    return this.children;
  },
  insertAdjacentHTML(_position, html) {
    inserted.push(html);
    const keyMatch = html.match(/data-kefu-message-key="([^"]+)"/);
    this.children.push({
      dataset: {
        kefuMessageKey: keyMatch ? keyMatch[1] : '',
      },
    });
  },
};

const context = {
  console,
  Number,
  document: {
    getElementById(id) {
      return id === 'kefu-chat-messages' ? box : null;
    },
  },
  fmtDate(value) {
    return String(value || '');
  },
  escHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  },
};

vm.runInNewContext(appendFunctionSource, context);

const userMessage = {
  id: 12345,
  sender_type: 1,
  content: 'same user message',
  create_time: '2026-05-02 10:00:00',
};

context.appendKefuChatMessage(userMessage);
context.appendKefuChatMessage(userMessage);

assert.strictEqual(inserted.length, 1, 'same kefu message id should render only once');
