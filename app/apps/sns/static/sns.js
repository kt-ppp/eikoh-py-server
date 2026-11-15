(() => {
    const userId = document.body.dataset.userId;
    const list = document.getElementById("posts");
    const error = document.getElementById("error");
    const status = document.getElementById("connection-status");
    let socket;
    let pendingForm = null;

    function mutationForm(action, id) {
        const form = document.createElement("form");
        form.method = "post";
        form.action = `/app/sns/posts/${id}/${action}`;
        form.dataset.action = action;
        form.dataset.id = id;
        return form;
    }

    function postElement(post) {
        const article = document.createElement("article");
        article.className = "post";
        article.dataset.id = post.id;
        const name = document.createElement("strong");
        name.className = "post-name";
        const text = document.createElement("p");
        text.className = "post-text";
        article.append(name, text);
        if (post.user_id === userId) {
            const actions = document.createElement("div");
            actions.className = "post-actions";
            const details = document.createElement("details");
            const summary = document.createElement("summary");
            summary.textContent = "編集";
            const edit = mutationForm("edit", post.id);
            const label = document.createElement("label");
            label.htmlFor = `edit-${post.id}`;
            label.textContent = "投稿を編集";
            const textarea = document.createElement("textarea");
            textarea.id = label.htmlFor;
            textarea.name = "text";
            textarea.rows = 3;
            textarea.maxLength = 500;
            textarea.required = true;
            const save = document.createElement("button");
            save.type = "submit";
            save.textContent = "保存する";
            edit.append(label, textarea, save);
            details.append(summary, edit);
            const remove = mutationForm("delete", post.id);
            const button = document.createElement("button");
            button.type = "submit";
            button.textContent = "削除";
            remove.append(button);
            actions.append(details, remove);
            article.append(actions);
        }
        return article;
    }

    function renderPosts(posts) {
        const ids = new Set(posts.map(post => String(post.id)));
        for (const article of Array.from(list.children)) {
            if (!ids.has(article.dataset.id)) article.remove();
        }
        for (const post of posts) {
            let article = list.querySelector(`[data-id="${post.id}"]`);
            if (!article) {
                article = postElement(post);
                list.append(article);
            }
            // 名前・本文をHTMLとして解釈させない。
            article.querySelector(".post-name").textContent = post.name;
            article.querySelector(".post-text").textContent = post.text;
            const details = article.querySelector("details");
            if (details && !details.open) details.querySelector("textarea").value = post.text;
        }
    }

    function finishPending(succeeded) {
        if (!pendingForm) return;
        pendingForm.querySelector("button").disabled = false;
        if (succeeded) {
            if (pendingForm.dataset.action === "post") pendingForm.querySelector("textarea").value = "";
            if (pendingForm.dataset.action === "edit") {
                const details = pendingForm.closest("details");
                details.open = false;
                pendingForm.querySelector("textarea").value = details.closest("article").querySelector(".post-text").textContent;
            }
        }
        pendingForm = null;
    }

    document.addEventListener("submit", event => {
        const form = event.target;
        if (form.dataset.action === "delete" && !window.confirm("この投稿を削除しますか？")) {
            event.preventDefault();
            return;
        }
        if (!form.dataset.action || !socket || socket.readyState !== WebSocket.OPEN) return;
        event.preventDefault();
        if (pendingForm) return;
        const message = {action: form.dataset.action};
        if (form.dataset.id) message.id = form.dataset.id;
        const textarea = form.querySelector("textarea");
        if (textarea) message.text = textarea.value;
        error.hidden = true;
        pendingForm = form;
        form.querySelector("button").disabled = true;
        try {
            socket.send(JSON.stringify(message));
        } catch {
            finishPending(false);
            error.textContent = "送信できませんでした。少し待ってからやり直してください。";
            error.hidden = false;
        }
    });

    function connect() {
        const url = new URL("/app/sns/ws", location.href);
        url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
        socket = new WebSocket(url);
        status.hidden = false;
        status.textContent = "接続中…";
        socket.addEventListener("open", () => { status.textContent = "接続済み"; });
        socket.addEventListener("message", event => {
            const message = JSON.parse(event.data);
            if (message.type === "posts") renderPosts(message.posts);
            if (message.type === "ok") finishPending(true);
            if (message.type === "error") {
                error.textContent = message.message;
                error.hidden = false;
                finishPending(false);
            }
        });
        socket.addEventListener("close", event => {
            finishPending(false);
            if (event.code === 1008) {
                location.reload();
                return;
            }
            status.textContent = "接続が切れました。再接続しています…";
            setTimeout(connect, 1000);
        });
    }

    if (window.WebSocket) connect();
})();
