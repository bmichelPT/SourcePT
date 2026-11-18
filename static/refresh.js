const financial = document.querySelector("#financial");
let refreshing = false;

async function refreshFinancialData() {
    if (document.hidden || refreshing) return;
    refreshing = true;
    try {
        const response = await fetch(window.location.href, { cache: "no-store" });
        if (response.redirected) {
            window.location.assign(response.url);
            return;
        }
        if (!response.ok) return;
        const page = new DOMParser().parseFromString(await response.text(), "text/html");
        const updated = page.querySelector("#financial");
        if (updated && financial.innerHTML !== updated.innerHTML) {
            financial.replaceChildren(...updated.childNodes);
        }
    } catch {
        // Leave the last successful result visible and try again on the next interval.
    } finally {
        refreshing = false;
    }
}

window.setInterval(refreshFinancialData, 15000);
document.addEventListener("visibilitychange", refreshFinancialData);

