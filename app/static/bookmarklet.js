/**
 * Beldex Nexus Companion Bookmarklet
 * Drag this script or save as a bookmark in your browser:
 * When you are on https://nexus.vin (logged into your account), clicking this bookmarklet
 * reads the live BDX price/balance and sends it to your local Beldex Sentinel dashboard!
 */
(function() {
    try {
        const text = document.body.innerText;
        // Search for rate patterns in DOM
        const bdxMatch = text.match(/(?:BDX|Beldex)[\s\:\-\=]*([0-9]+(?:\.[0-9]+)?)/i);
        const inrMatch = text.match(/(?:₹|INR|Rs\.?)[\s\:\-\=]*([0-9]+(?:\.[0-9]+)?)/i);
        const usdMatch = text.match(/(?:\$|USDT|USD)[\s\:\-\=]*([0-9]+(?:\.[0-9]+)?)/i);

        let rate_usd = usdMatch ? parseFloat(usdMatch[1]) : null;
        let rate_inr = inrMatch ? parseFloat(inrMatch[1]) : null;

        if (!rate_usd && !rate_inr && bdxMatch) {
            const val = parseFloat(bdxMatch[1]);
            if (val < 1.0) rate_usd = val; // likely a USD price
            else rate_inr = val; // likely an INR price
        }

        if (!rate_usd && !rate_inr) {
            const promptVal = prompt(
                "Beldex Sentinel Companion:\nCouldn't auto-detect the rate on this page.\nEnter the current BDX rate shown on Nexus:",
                "0.0715"
            );
            if (promptVal) {
                rate_usd = parseFloat(promptVal);
            } else {
                return;
            }
        }

        const payload = {
            rate_usd: rate_usd,
            rate_inr: rate_inr,
            note: 'Nexus Tab: ' + window.location.hostname
        };

        fetch('http://localhost:8080/api/nexus/sync', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        })
        .then(res => res.json())
        .then(data => {
            alert('✅ Beldex Sentinel Synced!\nNexus Rate: ' + (rate_usd ? '$' + rate_usd : '₹' + rate_inr) + '\nDashboard updated in real-time.');
        })
        .catch(err => {
            alert('❌ Sentinel unreachable on http://localhost:8080.\nPlease verify Beldex Sentinel is running locally.');
        });
    } catch(e) {
        alert('Nexus Companion Error: ' + e.message);
    }
})();
