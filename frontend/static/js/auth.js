// This file is a part of Statrix
// Coding : Priyanshu Dey [@irisXDR]

const TOKEN_KEY = 'statrix_token';
const USER_KEY = 'statrix_user';

function setToken(token) {
    try { localStorage.setItem(TOKEN_KEY, token); } catch (_) {}
}

function getToken() {
    try { return localStorage.getItem(TOKEN_KEY); } catch (_) { return null; }
}

function removeToken() {
    try {
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(USER_KEY);
    } catch (_) {}
}

function setUser(user) {
    try { localStorage.setItem(USER_KEY, JSON.stringify(user)); } catch (_) {}
}

function isAuthenticated() {
    return !!getToken();
}

async function apiRequest(endpoint, options = {}) {
    const token = getToken();

    const headers = {
        'Content-Type': 'application/json',
        ...options.headers
    };

    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }

    const response = await fetch(`${API_BASE}${endpoint}`, {
        ...options,
        headers
    });

    if (response.status === 401) {
        removeToken();
        if (window.location.pathname !== '/edit') {
            window.location.href = '/edit';
        }
        throw new Error('Unauthorized');
    }

    return response;
}

async function handleLogin(event) {
    event.preventDefault();

    const form = event.target;
    const email = form.email.value;
    const password = form.password.value;
    const button = document.getElementById('login-button');
    const buttonText = button.querySelector('.btn-text');
    const buttonLoader = button.querySelector('.btn-loader');
    const errorDiv = document.getElementById('error-message');

    button.disabled = true;
    buttonText.style.display = 'none';
    buttonLoader.style.display = 'flex';
    errorDiv.style.display = 'none';

    try {
        const response = await apiRequest('/api/auth/login', {
            method: 'POST',
            body: JSON.stringify({ email, password })
        });

        if (response.ok) {
            const data = await response.json();
            setToken(data.access_token);

            const userResponse = await apiRequest('/api/auth/me');
            if (userResponse.ok) {
                const user = await userResponse.json();
                setUser(user);
            }

            const container = document.querySelector('.login-container');
            if (container) {
                container.style.borderColor = 'rgba(16, 185, 129, 0.6)';
                container.style.boxShadow = '0 0 40px rgba(16, 185, 129, 0.3), 0 8px 32px rgba(0, 0, 0, 0.3)';
                buttonText.textContent = 'Welcome back!';
                buttonText.style.display = 'flex';
                buttonLoader.style.display = 'none';
            }
            await new Promise(r => setTimeout(r, 600));
            window.location.href = '/edit/dashboard';
        } else {
            let detail = 'Login failed';
            try {
                const error = await response.json();
                detail = error.detail || detail;
            } catch (_) {}
            throw new Error(detail);
        }
    } catch (error) {
        const msg = error.message || 'Invalid email or password';
        errorDiv.textContent = msg === 'Login failed' ? 'Hmm, that didn\'t work. Double-check your credentials.' : msg;
        errorDiv.style.display = 'block';

        const container = document.querySelector('.login-container');
        if (container) {
            container.classList.remove('shake');
            void container.offsetWidth; // force reflow to re-trigger animation
            container.classList.add('shake');
        }

        button.disabled = false;
        buttonText.style.display = 'flex';
        buttonLoader.style.display = 'none';
    }
}

async function handleLogout() {
    removeToken();
    window.location.href = '/edit';
}

document.addEventListener('DOMContentLoaded', () => {
    const loginForm = document.getElementById('login-form');
    if (loginForm) {
        loginForm.addEventListener('submit', handleLogin);
    }

    if (window.location.pathname.startsWith('/edit/dashboard') && !isAuthenticated()) {
        window.location.href = '/edit';
    }

    const logoutButton = document.getElementById('logout-button');
    if (logoutButton) {
        logoutButton.addEventListener('click', handleLogout);
    }
});
