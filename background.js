```javascript
let comments = {};

chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.sync.set({ comments: {} });
});

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.message === 'SAVE_COMMENT') {
    saveComment(request.data);
  } else if (request.message === 'LOAD_COMMENTS') {
    loadComments(request.data);
  }
});

function saveComment(data) {
  comments[data.url] = comments[data.url] || [];
  comments[data.url].push(data.comment);
  chrome.storage.sync.set({ comments: comments });
}

function loadComments(url) {
  chrome.storage.sync.get(['comments'], function(result) {
    if (result.comments && result.comments[url]) {
      chrome.runtime.sendMessage({
        message: 'LOAD_COMMENTS',
        data: result.comments[url]
      });
    }
  });
}
```