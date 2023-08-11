```javascript
// Options.js

// Function to save options
function saveOptions() {
    let comments = document.getElementById('commentInput').value;
    chrome.storage.sync.set({
        comments: comments
    }, function() {
        let status = document.getElementById('status');
        status.textContent = 'Options saved.';
        setTimeout(function() {
            status.textContent = '';
        }, 750);
    });
}

// Function to restore options
function restoreOptions() {
    chrome.storage.sync.get({
        comments: ''
    }, function(items) {
        document.getElementById('commentInput').value = items.comments;
    });
}

// Event listeners
document.addEventListener('DOMContentLoaded', restoreOptions);
document.getElementById('saveButton').addEventListener('click', saveOptions);
```