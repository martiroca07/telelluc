const terminal = document.getElementById('terminal');
const outputWrap = document.getElementById('output-wrap');
const output = document.getElementById('output');
const typed = document.getElementById('typed');
const hiddenInput = document.getElementById('hidden-input');
const promptLabel = document.getElementById('prompt-label');
const suggestionsBar = document.getElementById('suggestions');
const cornerBanner = document.getElementById('corner-banner');

const host = 'telelluc';
const user = 'visitante';
const history = [];
let historyIndex = -1;
let lang = 'en';
let bannerEnabled = true;

const strings = {
    en: {
        welcome: 'Welcome. Type "help" to see the available commands.',
        helpHeader: 'Available commands:',
        helpLines: [
            'help          show this help',
            'echo [text]   repeat the text',
            'date          show the current date and time',
            'whoami        show the current user',
            'clear / cls   clear the screen',
            'showbanner    toggle the rainbow "telelluc" text (top-right)',
            'language      switch the interface language (English/Español)',
            'crash         trigger the local telelluc.py error popup'
        ],
        notFound: (cmd) => `command not found: ${cmd}`,
        tryHelp: 'type "help" to see the available commands',
        langSwitched: 'Language set to English.',
        crashFailed: 'Could not reach telelluc.py — is it running?'
    },
    es: {
        welcome: 'Bienvenido. Escribí "help" para ver los comandos disponibles.',
        helpHeader: 'Comandos disponibles:',
        helpLines: [
            'help          muestra esta ayuda',
            'echo [texto]  repite el texto',
            'date          muestra la fecha y hora actual',
            'whoami        muestra el usuario actual',
            'clear / cls   limpia la pantalla',
            'showbanner    activa/desactiva el texto "telelluc" (arcoíris, arriba a la derecha)',
            'language      cambia el idioma de la interfaz (English/Español)',
            'crash         dispara el error emergente de telelluc.py'
        ],
        notFound: (cmd) => `comando no encontrado: ${cmd}`,
        tryHelp: 'escribí "help" para ver los comandos disponibles',
        langSwitched: 'Idioma cambiado a Español.',
        crashFailed: 'No se pudo contactar a telelluc.py — ¿está corriendo?'
    }
};

function t() {
    return strings[lang];
}

function updatePrompt() {
    promptLabel.innerHTML =
        `<span class="p-user">${user}@${host}</span>` +
        `<span class="p-sep">:</span>` +
        `<span class="p-path">~</span>` +
        `<span class="p-sep">$</span>`;
}

function print(text = '', className = '') {
    const div = document.createElement('div');
    div.className = 'line' + (className ? ' ' + className : '');
    div.textContent = text;
    output.appendChild(div);
}

function scrollToBottom() {
    outputWrap.scrollTop = outputWrap.scrollHeight;
}

const commands = {
    help() {
        print(t().helpHeader);
        t().helpLines.forEach((line) => print('  ' + line));
    },
    showbanner() {
        bannerEnabled = !bannerEnabled;
        cornerBanner.classList.toggle('hidden', !bannerEnabled);
    },
    echo(args) {
        print(args.join(' '));
    },
    date() {
        const now = new Date();
        print(lang === 'es' ? now.toLocaleString('es-ES') : now.toLocaleString('en-US'));
    },
    whoami() {
        print(user);
    },
    clear() {
        output.innerHTML = '';
    },
    language() {
        lang = lang === 'en' ? 'es' : 'en';
        print(t().langSwitched, 'dim');
    },
    crash() {
        fetch('http://127.0.0.1:5005/crash').catch(() => {
            print(t().crashFailed, 'error');
        });
    }
};
commands.cls = commands.clear;

const commandNames = Object.keys(commands).sort();

function runCommand(raw) {
    const trimmed = raw.trim();
    if (trimmed.length > 0) {
        history.push(trimmed);
    }
    historyIndex = history.length;

    const echoLine = document.createElement('div');
    echoLine.className = 'prompt-line';
    const p = document.createElement('span');
    p.className = 'prompt';
    p.innerHTML = promptLabel.innerHTML;
    echoLine.appendChild(p);
    const cmdText = document.createElement('span');
    cmdText.textContent = raw;
    echoLine.appendChild(cmdText);
    output.appendChild(echoLine);

    if (trimmed.length === 0) return;

    const [cmd, ...args] = trimmed.split(/\s+/);
    const fn = commands[cmd.toLowerCase()];
    if (fn) {
        fn(args);
    } else {
        print(t().notFound(cmd), 'error');
        print(t().tryHelp, 'dim');
    }
}

function refreshTyped() {
    typed.textContent = hiddenInput.value;
}

let suggestions = [];
let suggestionIndex = 0;

function computeSuggestions(value) {
    const v = value.toLowerCase();
    if (!v) return [];
    return commandNames.filter((name) => name.startsWith(v));
}

function renderSuggestions() {
    suggestionsBar.innerHTML = '';
    if (suggestions.length === 0) {
        suggestionsBar.classList.remove('visible');
        return;
    }
    suggestions.forEach((name, i) => {
        const span = document.createElement('span');
        span.className = 'suggestion' + (i === suggestionIndex ? ' active' : '');
        span.textContent = name;
        suggestionsBar.appendChild(span);
    });
    suggestionsBar.classList.add('visible');
}

function updateSuggestionsFromInput() {
    suggestions = computeSuggestions(hiddenInput.value);
    suggestionIndex = 0;
    renderSuggestions();
}

hiddenInput.addEventListener('input', () => {
    refreshTyped();
    updateSuggestionsFromInput();
    scrollToBottom();
});

hiddenInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
        e.preventDefault();
        const value = hiddenInput.value;
        hiddenInput.value = '';
        refreshTyped();
        suggestions = [];
        renderSuggestions();
        runCommand(value);
        scrollToBottom();
    } else if (e.key === 'Tab') {
        e.preventDefault();
        if (suggestions.length === 0) return;
        hiddenInput.value = suggestions[suggestionIndex];
        refreshTyped();
        updateSuggestionsFromInput();
    } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (suggestions.length > 0) {
            suggestionIndex = (suggestionIndex - 1 + suggestions.length) % suggestions.length;
            renderSuggestions();
            return;
        }
        if (history.length === 0) return;
        historyIndex = Math.max(0, historyIndex - 1);
        hiddenInput.value = history[historyIndex] || '';
        refreshTyped();
    } else if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (suggestions.length > 0) {
            suggestionIndex = (suggestionIndex + 1) % suggestions.length;
            renderSuggestions();
            return;
        }
        if (history.length === 0) return;
        historyIndex = Math.min(history.length, historyIndex + 1);
        hiddenInput.value = history[historyIndex] || '';
        refreshTyped();
    } else if (e.key === 'Escape') {
        suggestions = [];
        renderSuggestions();
    }
});

terminal.addEventListener('click', () => {
    hiddenInput.focus();
});

window.addEventListener('load', () => {
    hiddenInput.focus();
    updatePrompt();
    print(t().welcome, 'dim');
});
