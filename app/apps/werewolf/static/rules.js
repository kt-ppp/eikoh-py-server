(() => {
    const dialog = document.getElementById("rules-dialog");
    document.getElementById("open-rules").addEventListener("click", () => dialog.showModal());
    dialog.addEventListener("click", event => {
        if (event.target === dialog) {
            const bounds = dialog.getBoundingClientRect();
            if (event.clientX < bounds.left || event.clientX > bounds.right ||
                event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
        }
    });
})();
