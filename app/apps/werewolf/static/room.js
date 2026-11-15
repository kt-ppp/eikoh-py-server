(() => {
    const byId = id => document.getElementById(id);
    const code = document.body.dataset.roomCode;
    const userId = document.body.dataset.userId;
    const initial = JSON.parse(byId("initial-room").textContent);
    let room = initial.room;
    let clockOffset = initial.server_time - Date.now() / 1000;
    let socket;
    let lastSettings = "";
    let pending = null;
    let leaving = false;

    const help = {
        lobby: "4人以上そろうと始められます。",
        night: "対象を選んで決定してください。",
        discussion: "誰が人狼か話し合いましょう。",
        vote: "処刑したい人に投票します。",
        finished: "",
    };

    function showError(message) {
        byId("error").textContent = message;
        byId("error").hidden = false;
    }

    function listItems(id, texts) {
        const list = byId(id);
        const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
        const scroll = list.scrollTop;
        list.replaceChildren(...texts.map(text => {
            const item = document.createElement("li");
            item.textContent = text;
            return item;
        }));
        list.scrollTop = atBottom ? list.scrollHeight : scroll;
    }

    function timer() {
        byId("countdown").textContent = room.deadline === null ? "" :
            `残り ${Math.max(0, Math.ceil(room.deadline - Date.now() / 1000 - clockOffset))} 秒`;
    }

    function render(next) {
        room = next;
        const isOwner = room.owner_id === userId;
        const you = room.you;
        byId("phase-title").textContent = room.day ? `${room.day}日目 · ${room.phase_label}` : room.phase_label;
        byId("phase-help").textContent = help[room.phase];
        byId("winner").textContent = room.winner ?
            {village: "村人陣営の勝利", wolves: "人狼陣営の勝利", draw: "引き分け"}[room.winner] : "";
        const canSkip = ["night", "discussion", "vote"].includes(room.phase);
        byId("skip-panel").hidden = !canSkip;
        byId("skip-status").textContent = `スキップ希望：${room.skip.agreed} / ${room.skip.required}人`;
        const skipButton = byId("skip-time");
        skipButton.hidden = !you.alive;
        skipButton.textContent = room.skip.you_agreed ? "スキップを取り消す" : "時間をスキップ";
        skipButton.disabled = pending?.button === skipButton ||
            (room.phase === "night" && !you.selection && !room.skip.you_agreed);
        byId("skip-help").textContent =
            you.alive && room.phase === "night" && !you.selection ? "対象を選んでからスキップできます。" : "";
        byId("role-name").textContent = you.role_name || "未配布";
        byId("alive-status").textContent = room.phase === "lobby" ? "" : (you.alive ? "生存" : "死亡");
        byId("role-description").textContent = you.description;
        byId("teammates").textContent = you.teammates.length ? `仲間の人狼：${you.teammates.map(player => player.name).join("、")}` : "";
        listItems("private-log", room.private_log.length ? room.private_log : ["まだありません。"]);
        byId("player-count").textContent = `${room.players.length} / ${room.settings.capacity}人`;
        listItems("players", room.players.map(player => `${player.name}${player.user_id === userId ? "（あなた）" : ""}${player.user_id === room.owner_id ? "（作成者）" : ""} · ${player.alive ? "生存" : "死亡"}${player.voted ? " · 投票済み" : ""}${player.role ? ` · ${player.role}` : ""}`));
        const roleSummary = [
            `人狼${room.settings.wolves}`,
            room.settings.seer ? `占い師${room.settings.seer}` : "",
            room.settings.guard ? `騎士${room.settings.guard}` : "",
            room.settings.medium ? `霊媒師${room.settings.medium}` : "",
            room.settings.madman ? `狂人${room.settings.madman}` : "",
            "残り村人",
        ].filter(Boolean).join("・");
        byId("settings-summary").textContent = `${roleSummary} ／ 夜${room.settings.night_seconds}秒・議論${room.settings.discussion_seconds}秒・投票${room.settings.vote_seconds}秒`;
        byId("owner-panel").hidden = !(isOwner && room.phase === "lobby");
        byId("restart-panel").hidden = !(isOwner && room.phase === "finished");
        byId("leave-form").hidden = !["lobby", "finished"].includes(room.phase);
        const serialized = JSON.stringify(room.settings);
        if (lastSettings !== serialized) {
            for (const [key, value] of Object.entries(room.settings)) byId("settings-form").elements.namedItem(key).value = value;
            lastSettings = serialized;
        }
        byId("action-form").hidden = !room.candidates.length;
        const target = byId("target");
        const selection = target.value || you.selection || "";
        target.replaceChildren(...room.candidates.map(player => new Option(player.name, player.user_id)));
        if (room.candidates.some(player => player.user_id === selection)) target.value = selection;
        byId("target-label").textContent = room.phase === "vote" ? "投票する相手" :
            {wolf: "襲撃する相手", seer: "占う相手", guard: "守る相手"}[you.role] || "見回る相手（ダミー）";
        byId("action-help").textContent =
            room.phase === "night" && ["villager", "medium", "madman"].includes(you.role) ?
                "どの人を選んでも結果には影響しません。" : "";
        byId("selection-status").textContent = you.selection ? "選択済み" : "";
        const wolfChat = room.phase === "night" && you.role === "wolf" && you.alive;
        const canChat = you.alive && (["lobby", "discussion", "vote"].includes(room.phase) || wolfChat);
        byId("chat-title").textContent = wolfChat ? "人狼チャット" : "公開チャット";
        const messages = wolfChat ? room.wolf_chat : room.chat;
        listItems("messages", messages.map(message => `${message.name}：${message.text}`));
        byId("chat-text").disabled = !canChat;
        byId("chat-form").querySelector("button").disabled = !canChat || pending?.action === "chat";
        byId("chat-help").textContent = canChat ? "" : "今は発言できません。";
        listItems("game-log", room.log);
        timer();
    }

    function finishPending(success) {
        if (!pending) return;
        if (success && pending.action === "chat") byId("chat-text").value = "";
        pending.button.disabled = false;
        pending = null;
        render(room);
    }

    function send(command, button) {
        if (!socket || socket.readyState !== WebSocket.OPEN) {
            showError("通信が切れています。接続が戻ってからもう一度お試しください。");
            return;
        }
        if (pending) return;
        byId("error").hidden = true;
        pending = {action: command.action, button};
        button.disabled = true;
        try {
            socket.send(JSON.stringify(command));
        } catch {
            finishPending(false);
            showError("送信できませんでした。少し待ってからやり直してください。");
        }
    }

    byId("settings-form").addEventListener("submit", event => {
        event.preventDefault();
        const settings = Object.fromEntries(Array.from(new FormData(event.target), ([key, value]) => [key, Number(value)]));
        send({action: "configure", settings}, event.target.querySelector("button"));
    });
    byId("start-game").addEventListener("click", event => {
        const draft = Object.fromEntries(Array.from(new FormData(byId("settings-form")), ([key, value]) => [key, Number(value)]));
        if (Object.entries(draft).some(([key, value]) => room.settings[key] !== value)) {
            showError("変更した設定を保存してから開始してください。");
            return;
        }
        send({action: "start"}, event.target);
    });
    byId("reset-game").addEventListener("click", event => send({action: "reset"}, event.target));
    byId("skip-time").addEventListener("click", event => {
        send({action: "skip", phase_id: room.phase_id, agreed: !room.skip.you_agreed}, event.target);
    });
    byId("action-form").addEventListener("submit", event => {
        event.preventDefault();
        send({action: room.phase === "vote" ? "vote" : "night", phase_id: room.phase_id, target: byId("target").value}, event.target.querySelector("button"));
    });
    byId("chat-form").addEventListener("submit", event => {
        event.preventDefault();
        const channel = room.phase === "night" ? "wolves" : "public";
        send({action: "chat", channel, text: byId("chat-text").value}, event.target.querySelector("button"));
    });
    byId("leave-form").addEventListener("submit", () => { leaving = true; });
    byId("room-link").value = location.href;
    byId("copy-link").addEventListener("click", async () => {
        try {
            await navigator.clipboard.writeText(byId("room-link").value);
            byId("copy-status").textContent = "コピーしました。";
        } catch {
            byId("room-link").select();
            byId("copy-status").textContent = "選択したリンクをコピーしてください。";
        }
    });

    function connect() {
        if (leaving) return;
        const url = new URL(`/app/werewolf/rooms/${code}/ws`, location.href);
        url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
        socket = new WebSocket(url);
        byId("connection-status").textContent = "接続中…";
        socket.addEventListener("open", () => { byId("connection-status").textContent = "接続済み"; });
        socket.addEventListener("message", event => {
            const message = JSON.parse(event.data);
            if (message.type === "room") {
                clockOffset = message.server_time - Date.now() / 1000;
                render(message.room);
            } else if (message.type === "error") {
                showError(message.message);
                finishPending(false);
            } else if (message.type === "ok") {
                finishPending(true);
            } else if (message.type === "left") {
                leaving = true;
                location.assign("/app/werewolf/");
            }
        });
        socket.addEventListener("close", event => {
            finishPending(false);
            if (leaving) return;
            if ([1008, 4004].includes(event.code)) {
                leaving = true;
                location.assign("/app/werewolf/");
                return;
            }
            byId("connection-status").textContent = "再接続しています…";
            setTimeout(connect, 1000);
        });
    }

    render(room);
    setInterval(timer, 500);
    connect();
})();
