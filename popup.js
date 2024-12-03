```javascript
let comments = {};

document.addEventListener('DOMContentLoaded', function() {
    document.getElementById('saveButton').addEventListener('click', saveComment);
    document.getElementById('optionsButton').addEventListener('click', openOptions);
    loadComments();
});

function saveComment() {
    let commentInput = document.getElementById('commentInput');
    let commentText = commentInput.value.trim();
    if (commentText === '') return;

    let url = window.location.href;
    let timestamp = new Date().toISOString();

    let comment = {
        url: url,
        text: commentText,
        timestamp: timestamp
    };

    if (!comments[url]) {
        comments[url] = [];
    }

    comments[url].push(comment);
    chrome.storage.sync.set({comments: comments}, function() {
        commentInput.value = '';
        displayComments();
    });
}

function loadComments() {
    chrome.storage.sync.get('comments', function(data) {
        comments = data.comments || {};
        displayComments();
    });
}

function displayComments() {
    let url = window.location.href;
    let commentList = document.getElementById('commentList');
    commentList.innerHTML = '';

    if (comments[url]) {
        comments[url].forEach(function(comment) {
            let listItem = document.createElement('li');
            listItem.textContent = comment.text;
            commentList.appendChild(listItem);
        });
    }
}

function openOptions() {
    chrome.runtime.openOptionsPage();
}
```