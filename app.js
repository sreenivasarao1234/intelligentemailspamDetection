// --- Configuration ---
const API_BASE = window.location.protocol.startsWith("http")
    ? window.location.origin
    : "http://127.0.0.1:5000";

// --- DOM References ---
const emailText        = document.getElementById("emailText");
const charCount        = document.getElementById("charCount");
const scanBtn          = document.getElementById("scanBtn");
const resultSection    = document.getElementById("resultSection");
const resultCard       = document.getElementById("resultCard");
const resultIcon       = document.getElementById("resultIcon");
const resultVerdict    = document.getElementById("resultVerdict");
const resultDesc       = document.getElementById("resultDesc");
const confidenceValue  = document.getElementById("confidenceValue");
const confidenceFill   = document.getElementById("confidenceFill");
const accuracyValue    = document.getElementById("accuracyValue");
const accuracyFill     = document.getElementById("accuracyFill");
const textareaWrapper  = document.getElementById("textareaWrapper");
const soundToggleBtn   = document.getElementById("soundToggleBtn");
const soundIconOn      = document.getElementById("soundIconOn");
const soundIconOff     = document.getElementById("soundIconOff");

// --- Audio Configuration ---
let isSoundEnabled = true;
const safeTone = new Audio('safe_mail_tone.mp3');
const warningBeep = new Audio('warning_beep.mp3');

soundToggleBtn.addEventListener("click", () => {
    isSoundEnabled = !isSoundEnabled;
    soundToggleBtn.classList.toggle("muted", !isSoundEnabled);
    soundIconOn.style.display = isSoundEnabled ? "block" : "none";
    soundIconOff.style.display = isSoundEnabled ? "none" : "block";
    
    // Stop any playing sounds if user mutes
    if (!isSoundEnabled) {
        safeTone.pause();
        safeTone.currentTime = 0;
        warningBeep.pause();
        warningBeep.currentTime = 0;
    }
});

// --- Character counter ---
emailText.addEventListener("input", () => {
    const len = emailText.value.length;
    charCount.textContent = `${len.toLocaleString()} / 5,000`;
    charCount.classList.toggle("warn", len > 4000 && len <= 4800);
    charCount.classList.toggle("danger", len > 4800);
});

// --- Main scan function ---
function checkSpam() {
    const text = emailText.value.trim();

    // Empty validation with shake effect
    if (text === "") {
        textareaWrapper.classList.add("shake");
        textareaWrapper.style.borderColor = "var(--red)";
        setTimeout(() => {
            textareaWrapper.classList.remove("shake");
            textareaWrapper.style.borderColor = "";
        }, 600);
        return;
    }

    // Enter loading state
    scanBtn.classList.add("loading");
    resultSection.classList.remove("visible");

    fetch(`${API_BASE}/predict`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text })
    })
    .then(res => {
        if (!res.ok) throw new Error(`Server responded with ${res.status}`);
        return res.json();
    })
    .then(data => {
        scanBtn.classList.remove("loading");

        if (data.error) {
            showError(data.error);
            return;
        }

        showResult(data.prediction, data.confidence, data.model_accuracy);
    })
    .catch(err => {
        console.error("Prediction error:", err);
        scanBtn.classList.remove("loading");
        showError("Could not reach the backend. Is server.py running?");
    });
}

// --- Sound Playback ---
function playFeedbackSound(type) {
    if (!isSoundEnabled) return;

    // Prevent overlapping by stopping any current playback
    safeTone.pause();
    safeTone.currentTime = 0;
    warningBeep.pause();
    warningBeep.currentTime = 0;

    const audioPromise = (type === "spam") ? warningBeep.play() : safeTone.play();
    
    if (audioPromise !== undefined) {
        audioPromise.catch(error => {
            console.warn("Audio playback blocked by browser format/policy constraint:", error);
        });
    }
}

// --- Display result ---
function showResult(prediction, confidence, modelAccuracy) {
    const isSpam = prediction === "spam";
    const pct    = confidence ? (confidence * 100) : 0;

    // Play appropriate sound
    playFeedbackSound(isSpam ? "spam" : "safe");

    // Card styling
    resultCard.className = `result-card ${isSpam ? "spam" : "ham"}`;

    // Icon
    resultIcon.innerHTML = isSpam ? "🚨" : "✅";

    // Verdict
    resultVerdict.textContent = isSpam
        ? "Spam Detected"
        : "Email is Safe";

    // Description
    resultDesc.textContent = isSpam
        ? "This message exhibits patterns commonly found in unsolicited or malicious emails."
        : "This message does not match known spam patterns. It appears to be legitimate.";

    // Confidence bar
    confidenceValue.textContent = `${pct.toFixed(1)}%`;

    // Determine bar tier
    confidenceFill.className = "confidence-fill";
    if (pct < 50)       confidenceFill.classList.add("low");
    else if (pct < 70)  confidenceFill.classList.add("medium");
    else if (pct < 90)  confidenceFill.classList.add("high");
    else                confidenceFill.classList.add("very-high");

    // Animate fill width
    confidenceFill.style.width = "0%";
    requestAnimationFrame(() => {
        requestAnimationFrame(() => {
            confidenceFill.style.width = `${pct}%`;
        });
    });

    // Show result section
    resultSection.classList.add("visible");
    resultSection.scrollIntoView({ behavior: "smooth", block: "nearest" });

    // Model accuracy bar
    if (modelAccuracy) {
        const accPct = (modelAccuracy * 100);
        accuracyValue.textContent = `${accPct.toFixed(2)}%`;
        accuracyFill.style.width = "0%";
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                accuracyFill.style.width = `${accPct}%`;
            });
        });
    } else {
        accuracyValue.textContent = "—";
        accuracyFill.style.width = "0%";
    }
}

// --- Display error ---
function showError(message) {
    resultCard.className = "result-card spam";
    resultIcon.innerHTML = "❌";
    resultVerdict.textContent = "Error";
    resultDesc.textContent = message;
    confidenceValue.textContent = "—";
    confidenceFill.style.width = "0%";
    accuracyValue.textContent = "—";
    accuracyFill.style.width = "0%";
    resultSection.classList.add("visible");
}