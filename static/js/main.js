// Маленькие клиентские улучшения, основная логика остаётся на сервере.
document.querySelectorAll('form[onsubmit]').forEach((form) => {
    form.addEventListener('submit', (event) => {
        if (!confirm('Подтвердить действие?')) {
            event.preventDefault();
        }
    });
});
