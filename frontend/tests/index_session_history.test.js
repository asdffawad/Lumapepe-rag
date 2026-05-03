const assert = require('assert');
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');

assert(html.includes('/api/sessions/history'), 'index.html should call user scoped session history endpoint');
assert(html.includes('SESSION_MESSAGES'), 'index.html should define session messages endpoint');
assert(html.includes('id="sessionHistoryList"'), 'index.html should render a session history list container');
assert(html.includes('function loadSessionHistory'), 'index.html should load session history');
assert(html.includes('function selectHistorySession'), 'index.html should restore a selected history session');
assert(html.includes('function startNewSession'), 'index.html should let the user start a new session without loading by session_id');
assert(html.includes('window.selectHistorySession = selectHistorySession'), 'history item clicks should be wired');
assert(html.includes('await selectHistorySession(historyItems[0].id'), 'index.html should auto-select latest user history item');
